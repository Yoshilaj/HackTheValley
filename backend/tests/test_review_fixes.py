"""Regression tests for the review findings (docs/HANDOFF.md "Review fixes applied")."""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings, check_production_settings, settings
from app.db.models import Appointment, Notification, Observation, Report, RiskAssessment
from app.integrations.google_calendar import FakeCalendar
from app.ml.engine import consecutive_windows, evaluate_pathway
from app.providers.email import FakeEmail
from app.providers.llm import FakeLlm
from app.services import booking_agent as ba
from app.services import risk_service as rs
from app.services.checkin_service import submit_checkin
from app.services.context_builder import build_contexts
from app.services.report_service import build_report_data, generate_report
from tests.test_report import headers_for, make_assessment, make_user
from tests.test_rules_helpers import AS_OF, bp_days, ctx, prior
from tests.booking_helpers import no_web  # noqa: F401,E402  (autouse: web channel off)

TODAY = date.today().isoformat()


def _notes(db, user, kind=None):
    q = select(Notification).where(Notification.user_id == user.id)
    if kind:
        q = q.where(Notification.kind == kind)
    db.expire_all()
    return list(db.scalars(q))


# ---------------------------------------------------------------- 1 + 3 + 8: daily log / observations


def test_checkin_later_submission_keeps_earlier_observation(db):
    user = make_user(db)
    day = date(2026, 10, 16)
    res = submit_checkin(db, user, {"palpitations": True}, on_date=day)
    o = next(x for x in res["observations"] if x.label == "palpitations")
    oid, created = o.id, o.created_at
    res2 = submit_checkin(db, user, {"mood": 3}, on_date=day)
    assert res2["daily_log"].flags == {"palpitations": True}
    db.expire_all()
    assert db.get(Observation, oid).created_at == created


# ---------------------------------------------------------------- 4: reminder after consent revoke

def test_reminder_not_sent_after_share_consent_revoked(client, db):
    user = make_user(db)
    assessment = make_assessment(db, user)
    email = FakeEmail()
    appt = ba.run_booking_agent(db, user, assessment, FakeLlm(), FakeCalendar(), None, email, "en")
    ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=email)
    assert appt.status == "requested"
    r = client.post("/api/consent", headers=headers_for(user), json={"purpose": "share_with_clinic", "granted": False})
    assert r.status_code == 200
    db.expire_all()
    out = FakeEmail()
    counts = ba.process_appointment_followups(db, now=datetime.now(timezone.utc) + timedelta(days=4), email=out)
    assert counts["cancelled"] >= 1  # the job scans every user's appointments in the shared test DB
    assert not [m for m in out.outbox if (m.tags or {}).get("appointment_id") == appt.id]
    db.refresh(appt)
    assert appt.status == "cancelled" and appt.agent_log[-1]["reason"] == "share_with_clinic consent revoked"
    assert any((n.payload or {}).get("reason") == "consent_revoked" for n in _notes(db, user, "appointment_failed"))


# ---------------------------------------------------------------- 5: share consent gate

def test_public_report_requires_share_consent_and_revoke_kills_link(client, db):
    user = make_user(db)
    report = generate_report(db, user, make_assessment(db, user), FakeLlm(), "en")
    token = report.share_token
    assert token and client.get(f"/r/{token}").status_code == 200
    client.post("/api/consent", headers=headers_for(user), json={"purpose": "share_with_clinic", "granted": False})
    db.expire_all()
    assert db.get(Report, report.id).share_token is None
    assert client.get(f"/r/{token}").status_code == 404
    listed = client.get("/api/reports", headers=headers_for(user)).json()
    assert listed[0]["share_url"] is None


def test_report_without_share_consent_has_no_token(client, db):
    user = make_user(db, consents=("data_processing", "ai_assessment"))
    report = generate_report(db, user, make_assessment(db, user), FakeLlm(), "en")
    assert report.share_token is None
    assert client.get("/api/reports", headers=headers_for(user)).json()[0]["share_url"] is None
    # consent granted later → regenerate gives it a link
    client.post("/api/consent", headers=headers_for(user), json={"purpose": "share_with_clinic", "granted": True})
    res = client.post(f"/api/reports/{report.assessment_id}/regenerate", headers=headers_for(user))
    assert res.status_code == 200 and res.json()["share_url"].startswith(settings.base_url)


# ---------------------------------------------------------------- 6 + 11: report content

def test_report_context_filtered_by_pathway_and_truncated(db):
    user = make_user(db)
    a = make_assessment(db, user)  # cardio, adds "palpitations at night"
    now = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add_all([
        Observation(user_id=user.id, kind="functional_change", label="getting_lost", free_text="got lost", created_at=now),
        Observation(user_id=user.id, kind="mood", label="suicidal_ideation", free_text="private", created_at=now),
        Observation(user_id=user.id, kind="symptom", label="chest_pain", created_at=now),
        Observation(user_id=user.id, kind="symptom", label="dizziness", free_text="x" * 400, created_at=now),
    ])
    db.commit()
    data = build_report_data(db, user, a, "en")
    by_label = {c["label"]: c for c in data.patient_context}
    assert "getting_lost" not in by_label and "suicidal_ideation" not in by_label
    assert "palpitations at night" in by_label
    assert by_label["chest_pain"]["details"] is None
    assert len(by_label["dizziness"]["details"]) <= 200
    assert "name" not in data.facts_for_llm()["patient"]


def test_clinic_email_note_is_fixed_template(db):
    """LLM free text (notify_user message, a bogus preference) never reaches the clinic e-mail."""
    from app.services.booking.slots import plan_slots
    from app.services.clinics import load_seed_clinics

    user = make_user(db)
    assessment = make_assessment(db, user)
    cal, email = FakeCalendar(), FakeEmail()
    bloor = next(c for c in load_seed_clinics() if c.id == "seed_bloor_heart")
    slots = [s.start.isoformat() for s in plan_slots(bloor, calendar=cal, use_freebusy=True).proposals]
    llm = FakeLlm(tool_script=[
        ("find_clinics", {}),
        ("propose_slots", {"clinic_id": "seed_bloor_heart", "slots": slots,
                           "preference": "Patient BP 180/110 with chest pain"}),
        ("notify_user", {"message": "Your BP 180/110 and chest pain need a cardiologist."}),
    ], final_text="Done.")
    appt = ba.run_booking_agent(db, user, assessment, llm, cal, None, email, "en")
    assert appt.status == "awaiting_approval"
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)
    msg = next(m for m in email.outbox if (m.tags or {}).get("kind") == "booking_request")
    assert "180/110" not in msg.text and "chest pain" not in msg.text
    assert ba.clinic_note("cardio", "en") in msg.text


# ---------------------------------------------------------------- 7: prior assessments by time window

def test_priors_loaded_by_time_window(db):
    u = make_user(db, consents=())
    now = datetime.now(timezone.utc)
    for i in range(15):
        db.add(RiskAssessment(user_id=u.id, pathway="cardio", level="watch", created_at=now - timedelta(hours=i + 1)))
    db.add(RiskAssessment(user_id=u.id, pathway="cardio", level="watch", created_at=now - timedelta(days=70)))
    db.commit()
    assert len(build_contexts(db, u, as_of=now)["cardio"].prior_assessments) == 15


def test_persistence_uses_latest_assessment_per_window():
    as_of = AS_OF
    # window 1 holds an older run with BP-01 and a later run without it → latest wins → not persistent
    assert consecutive_windows("BP-01", as_of, [prior(10, ["BP-01"]), prior(8, [])]) == 1
    assert consecutive_windows("BP-01", as_of, [prior(10, []), prior(8, ["BP-01"])]) == 2


# ---------------------------------------------------------------- 9: production e-mail

def test_production_requires_real_email_provider(monkeypatch):
    base = dict(dev_mode=False, jwt_secret="p" * 40, admin_secret="prod-admin", field_encryption_key="k",
                resend_api_key="", resend_from_email="")
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        check_production_settings(Settings(**base, email_provider="auto"))
    check_production_settings(Settings(**base, email_provider="fake"))  # explicit opt-out is allowed
    monkeypatch.setattr(settings, "dev_mode", False)
    res = FakeEmail().send("a@example.com", "s", "<p>x</p>")
    assert res.accepted is False and res.error == "fake provider in production"


# ---------------------------------------------------------------- 12: confirm link

def test_confirm_requires_requested_status_and_fresh_token(client, db):
    user = make_user(db)
    slots = [{"start": "2026-11-02T10:00:00+09:00", "end": "2026-11-02T10:30:00+09:00"}] * 3
    appt = Appointment(user_id=user.id, status="proposed", channel="email", proposed_slots=slots, agent_log=[])
    db.add(appt)
    db.commit()
    with pytest.raises(ba.AppointmentStateError):
        ba.confirm_appointment(db, appt.id, 0, ba.confirm_token_for(appt, 0))
    res = client.post(f"/api/appointments/{appt.id}/confirm", params={"slot": 0, "token": ba.confirm_token_for(appt, 0)})
    assert res.status_code == 409
    old = ba.confirm_token(appt.id, issued_at=datetime.now(timezone.utc) - timedelta(days=31))
    assert not ba.verify_confirm_token(appt.id, old)
    assert ba.verify_confirm_token(appt.id, ba.confirm_token(appt.id))
    legacy = ba._token_sig(appt.id, 0)  # old format (no "ts.") is rejected
    assert not ba.verify_confirm_token(appt.id, legacy)


def test_confirm_token_secret_setting(monkeypatch):
    t = ba.confirm_token("a1")
    monkeypatch.setattr(settings, "appointment_token_secret", "another-secret")
    assert not ba.verify_confirm_token("a1", t)


# ---------------------------------------------------------------- 13: gate rules

def test_optional_inputs_do_not_make_cardio_insufficient():
    morning_only = bp_days(7, (122, 76), None)
    r = evaluate_pathway(ctx("cardio", bp_readings=morning_only))
    assert r.level == "no_finding"
    assert "bp_evening_readings" in r.data_quality["missing_optional_inputs"]
    assert evaluate_pathway(ctx("parkinsons")).level == "insufficient_data"
    assert evaluate_pathway(ctx("general")).level == "insufficient_data"


# ---------------------------------------------------------------- 15 + 16

def test_oauth_error_is_url_encoded(client):
    res = client.get("/api/integrations/google/callback", params={"error": "access denied&admin=1"},
                     follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"].endswith("reason=access%20denied%26admin%3D1")


def test_apple_health_upload_limit_enforced_while_streaming(client, db, monkeypatch):
    from app.api.routes import uploads

    monkeypatch.setattr(uploads, "MAX_EXPORT_BYTES", 1024)
    user = make_user(db)
    res = client.post("/api/uploads/apple-health", headers=headers_for(user),
                      files={"file": ("export.zip", io.BytesIO(b"x" * 5000), "application/zip")})
    assert res.status_code == 422
    d = Path(settings.storage_dir) / user.id / "apple_health"
    assert not d.exists() or list(d.iterdir()) == []
