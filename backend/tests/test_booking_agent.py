"""Booking agent v2: planning → approval → channels (FakeLlm / FakeCalendar / FakePlaces / FakeEmail)."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import CONSENT_PURPOSES, Appointment, AppointmentMessage
from app.integrations import Clinic
from app.integrations.google_calendar import FakeCalendar
from app.integrations.google_places import FakePlaces
from app.providers.email import EmailResult, FakeEmail
from app.providers.llm import FakeLlm
from app.services import booking_agent as ba
from app.services import followup
from app.services.booking.channels import ALLOWED_FIELDS, ChannelResult
from app.services.booking.channels.detect import DetectionResult
from app.services.booking.slots import plan_slots, within_opening_hours
from app.services.clinics import load_seed_clinics
from app.services.report_service import generate_report
from tests.booking_helpers import RAW_HEALTH, ScriptedChannel, clinic_mail, make_assessment, notes, toronto_user  # noqa: F401
from tests.booking_helpers import no_web  # noqa: F401  (autouse)

TOR = ZoneInfo("America/Toronto")
BLOOR = "seed_bloor_heart"
QUEEN = "seed_queen_west_cardio"


@pytest.fixture()
def setup(db):
    user = toronto_user(db)
    assessment = make_assessment(db, user)
    report = generate_report(db, user, assessment, FakeLlm(), "en")
    return user, assessment, report


def plan(db, user, assessment, report=None, **kw):
    kw.setdefault("calendar", FakeCalendar())
    kw.setdefault("email", FakeEmail())
    llm = kw.pop("llm", FakeLlm())
    return ba.run_booking_agent(db, user, assessment, llm, kw.pop("calendar"), kw.pop("places", None),
                                kw.pop("email"), "en", report=report, **kw)


def test_plan_stops_at_awaiting_approval_without_contacting_anyone(db, setup):
    user, assessment, report = setup
    cal, email = FakeCalendar(), FakeEmail()
    appt = plan(db, user, assessment, report, calendar=cal, email=email)
    assert appt.status == "awaiting_approval" and appt.channel is None
    assert clinic_mail(email.outbox) == [] and cal.events == {}
    assert db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id)).all() == []

    a = appt.approval
    assert a["status"] == "pending" and a["clinic"]["id"] == BLOOR and a["target_mode"] == "sandbox"
    by = {p["channel"]: p for p in a["channel_plan"]}
    assert [p["channel"] for p in a["channel_plan"]] == ["web", "email", "phone"]
    assert by["web"] == by["web"] | {"available": False, "reason": "not configured"}
    assert by["email"]["available"] and by["email"]["target"] == "booking@bloorheart.example.com"
    assert by["phone"]["reason"] == "not configured"
    assert set(a["shared_fields"]) <= set(ALLOWED_FIELDS)
    assert a["shared_fields"]["postal_code"] == "M5S 1X5"
    assert a["shared_fields"]["address"] == "401 Bloor St W, Toronto"  # given only on request (phone)
    # onboarding gaps the agent cannot answer (toronto_user has no legal name / city / street parts)
    assert {"first_name", "last_name"} <= set(a["missing_profile_fields"]) and "postal_code" not in a["missing_profile_fields"]
    assert a["shared_fields"]["reason_category"] == "Heart and blood-pressure check-up"
    assert f"/r/{report.share_token}" in a["shared_fields"]["report_url"]
    assert not any(raw in str(a["shared_fields"]) for raw in RAW_HEALTH)
    assert a["referral_note"] and "referral" in a["referral_note"]

    clinic = next(c for c in load_seed_clinics() if c.id == BLOOR)
    from app.integrations import Slot
    slots = [Slot.from_any(s) for s in appt.proposed_slots]
    assert len(slots) == 3 and len({s.start.astimezone(TOR).date() for s in slots}) == 3
    assert all(within_opening_hours(s, clinic.opening_hours, "America/Toronto") for s in slots)
    assert len(appt.candidates) == 5 and appt.candidates[0]["id"] == BLOOR
    # one approval covers the top 3 usable candidates, each with its own plan and slots
    cards = a["candidates"]
    assert [c["rank"] for c in cards] == [1, 2, 3] and cards[0]["clinic_id"] == BLOOR
    assert a["candidate_ids"] == [c["clinic_id"] for c in cards] and cards[1]["clinic_id"] == QUEEN
    assert all(len(c["slots"]) == 3 and [p["channel"] for p in c["channel_plan"]] == ["web", "email", "phone"]
               for c in cards)
    det = [e["tool"] for e in appt.agent_log if e.get("mode") == "deterministic"]
    assert det == ["find_clinics", "get_free_slots", "propose_slots"]
    assert any(e["type"] == "discovery" for e in appt.agent_log)
    n = notes(db, user, "appointment_approval_needed")
    assert len(n) == 1 and n[0].payload["appointment_id"] == appt.id and "Nothing is sent" in n[0].body
    assert "we will try Queen West Cardiology Associates" in n[0].body
    assert [c["rank"] for c in n[0].payload["candidates"]] == [1, 2, 3]


def test_llm_tool_script_plans_chosen_clinic(db, setup):
    user, assessment, report = setup
    cal = FakeCalendar()
    queen = next(c for c in load_seed_clinics() if c.id == QUEEN)
    picks = [s.start.isoformat() for s in plan_slots(queen, calendar=cal, use_freebusy=True).proposals]
    llm = FakeLlm(tool_script=[
        ("find_clinics", {}),
        ("check_channels", {"clinic_id": QUEEN}),
        ("get_free_slots", {"clinic_id": QUEEN}),
        ("propose_slots", {"clinic_id": "made_up", "slots": picks}),            # refused
        ("propose_slots", {"clinic_id": QUEEN, "slots": picks, "preference": "morning"}),
        ("notify_user", {"message": "I picked Queen West Cardiology, about 2 km away."}),
    ], final_text="Ready for approval.")
    appt = plan(db, user, assessment, report, calendar=cal, llm=llm)
    assert appt.status == "awaiting_approval" and appt.clinic["id"] == QUEEN
    assert [s["start"] for s in appt.proposed_slots] == sorted(picks)
    tools = [e for e in appt.agent_log if e.get("type") == "tool"]
    assert all(e["mode"] == "llm" for e in tools)
    assert [e["is_error"] for e in tools if e["tool"] == "propose_slots"] == [True, False]
    # LLM free text is only a labelled note; the card body comes from structured data
    assert appt.approval["agent_note"] == {"label": "AI-generated note (not verified)",
                                           "text": "I picked Queen West Cardiology, about 2 km away."}
    assert "user_message" not in appt.approval
    assert appt.approval["slot_preference"] == "morning"
    body = notes(db, user, "appointment_approval_needed")[0].body
    assert body.startswith(f"We found {appt.clinic['name']}") and "I picked" not in body


def test_approve_sends_one_clinic_email_and_tentative_event(db, setup):
    user, assessment, report = setup
    cal, email = FakeCalendar(), FakeEmail()
    appt = plan(db, user, assessment, report, calendar=cal, email=email)
    out = ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)
    assert out.status == "requested" and out.channel == "email" and out.approval["status"] == "approved"
    mails = clinic_mail(email.outbox)
    assert len(mails) == 1
    msg = mails[0]
    assert msg.to == ["booking@bloorheart.example.com"] and msg.reply_to == user.email  # no reply domain set
    body = msg.html + msg.text
    assert f"/r/{report.share_token}" in body and "M5S 1X5" in body and "1958-01-15" in body
    assert "+1 416-555-0199" in body and "Alex Tremblay" in body
    for i in range(3):
        m = re.search(rf"confirm\?slot={i}&token=([0-9]+\.[0-9a-f]+)", msg.text)
        assert m and ba.verify_confirm_token(appt.id, m.group(1), clinic_id=appt.clinic["id"], slot_index=i)
        assert not ba.verify_confirm_token(appt.id, m.group(1), clinic_id="another_clinic", slot_index=i)
    for raw in RAW_HEALTH:
        assert raw not in body, raw
    assert ba.clinic_note("cardio") in msg.text
    attempts = [(a["channel"], a["status"]) for a in out.channel_attempts]
    assert attempts == [("web", "skipped"), ("email", "requested")]
    assert out.reply_token and out.external_ref == msg.id
    assert out.clinic["request"]["to"] == "booking@bloorheart.example.com" and out.clinic["request"]["accepted"]
    assert cal.events == {} and out.calendar_event_id is None  # calendar writes wait for the user's OK
    rows = db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id)).all()
    assert [(r.direction, r.channel) for r in rows] == [("out", "email")]
    assert len(notes(db, user, "appointment_requested")) == 1
    with pytest.raises(ba.AppointmentStateError):
        ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)  # only once


def test_reply_domain_and_sandbox_inbox(db, setup, monkeypatch):
    user, assessment, report = setup
    monkeypatch.setattr(settings, "booking_reply_domain", "book.example.org")
    monkeypatch.setattr(settings, "sandbox_clinic_email", "team-inbox@example.org")
    email = FakeEmail()
    appt = plan(db, user, assessment, report, email=email)
    assert appt.approval["channel_plan"][1]["target"] == "team-inbox@example.org"
    assert appt.approval["channel_plan"][1]["redirected"] is True
    ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=email)
    msg = clinic_mail(email.outbox)[0]
    assert msg.to == ["team-inbox@example.org"] and msg.reply_to == f"book+{appt.reply_token}@book.example.org"
    assert msg.subject.startswith("[SANDBOX]") and "redirected from booking@bloorheart.example.com" in msg.html
    assert appt.channel_attempts[-1]["redirected"] is True


def test_channel_fall_through_order(db, setup):
    user, assessment, report = setup
    web = ScriptedChannel("web", result=ChannelResult.skipped("login wall appeared"))
    email = ScriptedChannel("email", result=ChannelResult.failed("bounced"))
    phone = ScriptedChannel("phone", result=ChannelResult(status="requested", reason="call placed",
                                                          external_ref="conv_123"))
    chans = {"web": web, "email": email, "phone": phone}
    appt = plan(db, user, assessment, report, channels=chans)
    assert [p["available"] for p in appt.approval["channel_plan"]] == [True, True, True]
    out = ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail(), channels=chans)
    assert out.status == "requested" and out.channel == "phone" and out.external_ref == "conv_123"
    assert [(a["channel"], a["status"]) for a in out.channel_attempts] == [
        ("web", "skipped"), ("email", "failed"), ("phone", "requested")]
    assert web.calls == email.calls == phone.calls == [BLOOR]


def test_blocked_channel_skipped_instantly_never_executed(db, setup):
    user, assessment, report = setup
    web = ScriptedChannel("web", reason="captcha")
    email = ScriptedChannel("email", result=ChannelResult(status="confirmed", reason="booked",
                                                          slot={"start": "2026-11-02T10:00:00-05:00",
                                                                "end": "2026-11-02T10:30:00-05:00"}))
    chans = {"web": web, "email": email, "phone": ScriptedChannel("phone", reason="not configured")}
    cal = FakeCalendar()
    appt = plan(db, user, assessment, report, channels=chans, calendar=cal)
    out = ba.approve_appointment(db, user, appt.id, calendar=cal, email=FakeEmail(), channels=chans)
    assert web.calls == [] and out.channel_attempts[0] == out.channel_attempts[0] | {"status": "skipped",
                                                                                     "reason": "captcha"}
    assert out.status == "confirmed" and out.chosen_slot["start"] == "2026-11-02T10:00:00-05:00"
    assert cal.events == {} and out.approval["calendar"]["status"] == "pending"
    assert len(notes(db, user, "appointment_confirmed")) == 1
    ba.add_to_calendar(db, user, appt.id, calendar=cal)
    assert cal.events[out.calendar_event_id]["status"] == "confirmed"


def test_all_channels_fail_next_candidate_needs_new_approval_then_failed(db, setup):
    user, assessment, report = setup
    chans = {"web": ScriptedChannel("web", reason="not configured"),
             "email": ScriptedChannel("email", result=ChannelResult.failed("bounced")),
             "phone": ScriptedChannel("phone", reason="not configured")}
    appt = plan(db, user, assessment, report, channels=chans)
    first = appt.clinic["id"]
    covered = list(appt.approval["candidate_ids"])
    out = ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail(), channels=chans)
    # the 3 approved clinics were tried without asking again; only then a new card for the rest
    assert [a["clinic_id"] for a in out.channel_attempts if a["channel"] == "email"] == covered
    assert out.status == "awaiting_approval" and out.clinic["id"] not in covered and first in covered
    assert out.approval["status"] == "pending" and len(notes(db, user, "appointment_approval_needed")) == 2
    assert any(e.get("auto_approved") for e in out.agent_log if e["type"] == "next_candidate")
    for _ in range(10):
        if out.status != "awaiting_approval":
            break
        out = ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail(), channels=chans)
    assert out.status == "failed" and len({a["clinic_id"] for a in out.channel_attempts}) == 5
    assert "no other clinic" in out.agent_log[-1]["reason"]
    assert len(notes(db, user, "appointment_failed")) == 1


def test_email_rejected_falls_to_next_candidate(db, setup):
    user, assessment, report = setup

    class Rejecting(FakeEmail):
        def send(self, *a, **kw):
            return EmailResult(id=None, provider="fake", accepted=False, error="bounced")

    appt = plan(db, user, assessment, report)
    covered = appt.approval["candidate_ids"]
    out = ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=Rejecting())
    failed = [a["clinic_id"] for a in out.channel_attempts if (a["channel"], a["status"]) == ("email", "failed")]
    assert failed == covered and covered[:2] == [BLOOR, QUEEN]
    assert out.status == "awaiting_approval" and out.clinic["id"] not in covered


def test_no_usable_channel_anywhere_fails_at_planning(db, setup):
    user, assessment, report = setup
    chans = {n: ScriptedChannel(n, reason="not configured") for n in ("web", "email", "phone")}
    appt = plan(db, user, assessment, report, channels=chans)
    assert appt.status == "failed" and "no candidate clinic" in appt.agent_log[-1]["reason"]
    assert len(notes(db, user, "appointment_failed")) == 1


def test_decline_cancels_and_sends_nothing(db, setup):
    user, assessment, report = setup
    email = FakeEmail()
    appt = plan(db, user, assessment, report, email=email)
    out = ba.decline_appointment(db, user, appt.id)
    assert out.status == "cancelled" and out.approval["status"] == "declined"
    assert clinic_mail(email.outbox) == []
    with pytest.raises(ba.AppointmentStateError):
        ba.approve_appointment(db, user, appt.id, email=email)


def test_approval_off_runs_immediately(db, setup, monkeypatch):
    user, assessment, report = setup
    monkeypatch.setattr(settings, "booking_require_approval", False)
    email = FakeEmail()
    appt = plan(db, user, assessment, report, email=email)
    assert appt.status == "requested" and appt.approval["approved_by"] == "auto"
    assert len(clinic_mail(email.outbox)) == 1


def test_places_clinic_email_found_on_website(db, setup, monkeypatch):
    user, assessment, report = setup
    monkeypatch.setattr(settings, "sandbox_clinic_email", "team-inbox@example.org")
    places = FakePlaces([Clinic(id="gp_1", name="Real-looking Heart Clinic", specialty="cardiology", lat=43.66,
                                lng=-79.40, website="https://realheart.ca", source="google_places")])
    seen = []

    def detector(url):
        seen.append(url)
        return DetectionResult(website=url, web="none", reason="no booking form found",
                               emails=["desk@realheart.ca"])

    email = FakeEmail()
    appt = plan(db, user, assessment, report, places=places, detector=detector, email=email)
    assert appt.clinic["id"] == "gp_1" and seen == ["https://realheart.ca"]  # detection reads the REAL site
    det_log = next(e for e in appt.agent_log if e["type"] == "detection")
    assert det_log["url"] == "https://realheart.ca" and "read-only" in det_log["note"]
    by = {p["channel"]: p for p in appt.approval["channel_plan"]}
    assert by["email"]["real"] == "desk@realheart.ca" and by["email"]["target"] == "team-inbox@example.org"
    assert appt.approval["detection"]["reason"] == "no booking form found"
    ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=email)
    assert clinic_mail(email.outbox)[0].to == ["team-inbox@example.org"]  # execution is sandboxed
    att = appt.channel_attempts[-1]
    assert (att["target"], att["real"], att["redirected"]) == ("team-inbox@example.org", "desk@realheart.ca", True)


def test_missing_share_consent_stores_nothing(db):
    user = toronto_user(db, consents=[p for p in CONSENT_PURPOSES if p != "share_with_clinic"])
    assessment = make_assessment(db, user)
    email, cal = FakeEmail(), FakeCalendar()
    assert ba.run_booking_agent(db, user, assessment, FakeLlm(), cal, None, email, "en") is None
    assert email.outbox == [] and cal.events == {}
    assert db.scalars(select(Appointment).where(Appointment.user_id == user.id)).first() is None


def test_missing_calendar_consent_uses_opening_hours_only(db):
    user = toronto_user(db, consents=[p for p in CONSENT_PURPOSES if p != "calendar_access"])
    assessment = make_assessment(db, user)
    email, cal = FakeEmail(), FakeCalendar()
    appt = ba.run_booking_agent(db, user, assessment, FakeLlm(), cal, None, email, "en")
    assert appt.status == "awaiting_approval" and cal.freebusy_calls == 0
    assert any("no calendar access" in (e.get("text") or "") for e in appt.agent_log)
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)
    assert appt.status == "requested" and cal.events == {} and appt.calendar_event_id is None


def test_busy_day_avoided(db, setup):
    user, assessment, report = setup
    first = plan(db, user, assessment, report)
    day = datetime.fromisoformat(first.proposed_slots[0]["start"]).astimezone(TOR).date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=TOR)
    from datetime import timedelta
    cal = FakeCalendar(busy=[(start, start + timedelta(days=1))])
    appt = plan(db, user, make_assessment(db, user, with_data=False), calendar=cal)
    assert all(datetime.fromisoformat(s["start"]).astimezone(TOR).date() != day for s in appt.proposed_slots)


def test_tokyo_profile_still_plans(db):
    """Users without a Canadian profile (legacy fixtures) still get the seed clinics."""
    from tests.test_report import make_user

    user = make_user(db)
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), FakeCalendar(), None, FakeEmail(), "en")
    assert appt.status == "awaiting_approval" and appt.clinic["id"] == BLOOR


def test_emergency_never_books(db):
    user = toronto_user(db)
    assessment = make_assessment(db, user, level="emergency", with_data=False)
    with pytest.raises(ValueError):
        ba.run_booking_agent(db, user, assessment, FakeLlm(), FakeCalendar(), None, FakeEmail(), "en")
    assert followup.handle_followup(db, user, assessment, llm=FakeLlm()) == (None, None)


def test_handle_followup_plans_once(db):
    user = toronto_user(db)
    email = FakeEmail()
    report, appt = followup.handle_followup(db, user, make_assessment(db, user), llm=FakeLlm(),
                                            calendar=FakeCalendar(), email=email, language="en")
    assert report is not None and appt.status == "awaiting_approval" and clinic_mail(email.outbox) == []
    report2, appt2 = followup.handle_followup(db, user, make_assessment(db, user, with_data=False), llm=FakeLlm(),
                                              calendar=FakeCalendar(), email=email, language="en")
    assert report2 is not None and appt2 is None  # awaiting_approval counts as active -> no second plan


def test_handle_followup_without_share_consent_only_reports(db):
    user = toronto_user(db, consents=["data_processing", "ai_assessment"])
    report, appt = followup.handle_followup(db, user, make_assessment(db, user), llm=FakeLlm(), email=FakeEmail())
    assert report is not None and appt is None


def test_register_followup_hooks_idempotent():
    from app.services.risk_service import FOLLOWUP_HOOKS

    assert followup.register_followup_hooks() is True
    assert followup.register_followup_hooks() is True
    assert FOLLOWUP_HOOKS.count(followup.followup_hook) == 1


# ---------------------------------------------------------------- the app's 85+ pop-up

def test_user_picks_one_channel_and_only_that_one_runs(db, setup):
    user, assessment, report = setup
    web = ScriptedChannel("web", result=ChannelResult(status="requested", reason="form sent"))
    email = ScriptedChannel("email", result=ChannelResult(status="requested", reason="sent"))
    phone = ScriptedChannel("phone", result=ChannelResult(status="requested", reason="call placed",
                                                          external_ref="conv_9"))
    chans = {"web": web, "email": email, "phone": phone}
    appt = plan(db, user, assessment, report, channels=chans)
    out = ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail(), channels=chans,
                                 channel="phone")
    assert out.status == "requested" and out.channel == "phone" and out.approval["chosen_channel"] == "phone"
    assert web.calls == email.calls == [] and phone.calls == [BLOOR]
    assert [a["channel"] for a in out.channel_attempts] == ["phone"]


def test_an_unavailable_channel_cannot_be_chosen(db, setup):
    user, assessment, report = setup
    chans = {"web": ScriptedChannel("web", reason="captcha"), "email": ScriptedChannel("email"),
             "phone": ScriptedChannel("phone", reason="not configured")}
    appt = plan(db, user, assessment, report, channels=chans)
    with pytest.raises(ValueError, match="web is not an available way"):
        ba.approve_appointment(db, user, appt.id, channels=chans, channel="web")
    db.refresh(appt)
    assert appt.status == "awaiting_approval"  # nothing claimed, nothing sent


def test_auto_endpoint_plans_once_per_pathway_and_lists_the_channels(client, db):
    from tests.test_report import headers_for

    user = toronto_user(db)
    make_assessment(db, user)
    res = client.post("/api/appointments/auto", json={"pathway": "cardio"}, headers=headers_for(user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "awaiting_approval"
    assert [p["channel"] for p in body["approval"]["channel_plan"]] == ["web", "email", "phone"]
    assert all("available" in p and "reason" in p for p in body["approval"]["channel_plan"])
    again = client.post("/api/appointments/auto", json={"pathway": "cardio"}, headers=headers_for(user)).json()
    assert again["id"] == body["id"]  # the active request, not a second one
    assert client.post("/api/appointments/auto", json={"pathway": "cognitive"},
                       headers=headers_for(user)).status_code == 404  # no assessment for it yet


def test_auto_endpoint_needs_consent_and_never_books_emergencies(client, db):
    from tests.test_report import headers_for

    no_consent = toronto_user(db, consents=("data_processing",))
    make_assessment(db, no_consent)
    res = client.post("/api/appointments/auto", json={"pathway": "cardio"}, headers=headers_for(no_consent))
    assert res.status_code == 403
    urgent = toronto_user(db)
    make_assessment(db, urgent, level="emergency")
    assert client.post("/api/appointments/auto", json={"pathway": "cardio"},
                       headers=headers_for(urgent)).status_code == 409
