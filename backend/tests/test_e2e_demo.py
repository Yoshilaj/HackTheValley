"""End-to-end demo walkthrough through the HTTP API with fake providers only (BACKEND_PLAN demo script).

planted trend → risk run → report + booking plan (awaiting approval) → user approves (clinic e-mail) →
dashboard → clinic confirm → chat (RAG +
observation) → red flag (emergency, no booking) → check-in → story / vowel / tapping tasks → export → delete.

Uses a fresh user with the demo profile + all consents (the shared session DB's demo@example.com may
already carry rows from other tests), seeded with ``scripts.seed_demo.seed_planted_trend``.
"""
from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

import pytest
import soundfile as sf
from sqlalchemy import func, select

from app.api.routes import chat as chat_routes
from app.api.routes import tasks as task_routes
from app.content.loader import get_story_by_id
from app.core.auth import create_access_token
from app.core.config import settings
from app.core.security import encrypt_str
from app.db.models import (
    CONSENT_PURPOSES, USER_OWNED_MODELS, Appointment, AssessmentTask, Consent, OAuthCredential, Profile, Report, User,
)
from app.ml.priority import SCORE_LABEL_I18N
from app.providers import email as email_mod
from app.providers.email import FakeEmail
from app.providers.embeddings import FakeEmbeddings
from app.providers.llm import FakeLlm
from app.providers.stt import FakeStt
from app.rag.ingest import ingest_corpus
from app.services import notify as notify_mod
from app.services.report_service import FRAMINGHAM_LABEL
from scripts.seed import DEMO_PROFILE, _dob_for_age
from scripts.seed_demo import seed_planted_trend
from tests.test_vowel_features import SR, synth_vowel
from tests.booking_helpers import no_web  # noqa: F401,E402  (autouse: web channel off)

SOURCES = Path(__file__).parent / "fixtures" / "rag" / "sources.json"
STORY_STEPS = ("story1_listen", "story1_recall", "story2_listen", "story2_recall", "fluency_60s", "delay",
               "story1_delayed_recall")


@pytest.fixture()
def outbox(monkeypatch):
    """One shared FakeEmail for the booking agent (lazy import) and notify (module import)."""
    fake = FakeEmail()
    monkeypatch.setattr(email_mod, "get_email_provider", lambda: fake)
    monkeypatch.setattr(notify_mod, "get_email_provider", lambda: fake)
    return fake


@pytest.fixture()
def demo(db):
    u = User(email=f"e2e-{uuid.uuid4().hex[:8]}@example.com")
    u.profile = Profile(**(DEMO_PROFILE | {"date_of_birth": _dob_for_age(68)}))
    db.add(u)
    db.flush()
    for purpose in CONSENT_PURPOSES:
        db.add(Consent(user_id=u.id, purpose=purpose))
    db.add(OAuthCredential(user_id=u.id, provider="google", encrypted_tokens=encrypt_str('{"refresh_token": "fake"}'),
                         scopes=["calendar"]))
    db.commit()
    seed_planted_trend(db, u)
    return u, {"Authorization": f"Bearer {create_access_token(u.id)}"}


@pytest.fixture()
def fakes(app, db):
    ingest_corpus(db, FakeEmbeddings(), SOURCES)
    state = {"llm": FakeLlm(canned_answer="Measure twice each morning after resting [1].",
                            extract_queue=[{"observations": [{"kind": "symptom", "label": "dizziness",
                                                              "severity": "mild", "onset": "yesterday"}]}]),
             "stt": FakeStt(text="")}
    app.dependency_overrides[chat_routes.get_llm] = lambda: state["llm"]
    app.dependency_overrides[chat_routes.get_embeddings] = lambda: FakeEmbeddings()
    app.dependency_overrides[task_routes.get_embeddings] = lambda: FakeEmbeddings()
    app.dependency_overrides[task_routes.get_stt] = lambda: state["stt"]
    yield state
    for dep in (chat_routes.get_llm, chat_routes.get_embeddings, task_routes.get_embeddings, task_routes.get_stt):
        app.dependency_overrides.pop(dep, None)


def _wav(y) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, y, SR, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _cards(client, h) -> dict:
    r = client.get("/api/dashboard?language=en", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _count(db, model, user_id) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))


def test_e2e_demo(client, app, db, demo, fakes, outbox):
    user, h = demo

    # ---- risk run: cardio follow-up with BP-02 + RHY-01
    r = client.post("/api/risk/run", headers=h)
    assert r.status_code == 200, r.text
    rows = {a["pathway"]: a for a in r.json()["assessments"]}
    cardio = rows["cardio"]
    assert cardio["level"] == "followup_recommended"
    assert {"BP-02", "RHY-01"} <= {f["id"] for f in cardio["fired_rules"]}
    assert cardio["priority_score"] >= 70

    # ---- report + public share link
    db.expire_all()
    report = db.scalar(select(Report).where(Report.user_id == user.id, Report.assessment_id == cardio["id"]))
    assert report is not None and report.share_token
    page = client.get(f"/r/{report.share_token}")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert any(SCORE_LABEL_I18N[k] in page.text for k in ("ja", "en")), "priority label missing"
    assert FRAMINGHAM_LABEL["en"] in page.text, "Framingham label missing"
    assert "CCS high risk" in page.text

    # ---- booking plan awaits the user's approval; nothing has been sent to a clinic yet
    appts = list(db.scalars(select(Appointment).where(Appointment.user_id == user.id)))
    assert len(appts) == 1
    appt = appts[0]
    assert appt.status == "awaiting_approval" and len(appt.proposed_slots) == 3
    assert not [m for m in outbox.outbox if (m.tags or {}).get("kind") == "booking_request"]
    kinds = {n["kind"] for n in client.get("/api/notifications?limit=100", headers=h).json()}
    assert "appointment_approval_needed" in kinds

    # ---- user approves → exactly one clinic e-mail with 3 slots, no raw health data
    r = client.post(f"/api/appointments/{appt.id}/approve", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "requested" and r.json()["channel"] == "email"
    db.expire_all()
    clinic_mail = [m for m in outbox.outbox if (m.tags or {}).get("kind") == "booking_request"]
    assert len(clinic_mail) == 1
    mail = clinic_mail[0]
    body = mail.html + (mail.text or "")
    assert f"/r/{report.share_token}" in body
    assert not re.search(r"\b1[3-6]\d\s*/\s*[89]\d\b", body), "BP values must not be in the clinic e-mail"
    for raw in ("BP-02", "RHY-01", "心房細動", "possible_af", "mmHg", "palpitations"):
        assert raw not in body, raw

    # ---- dashboard: next action + notifications
    dash = _cards(client, h)
    card = next(c for c in dash["pathways"] if c["pathway"] == "cardio")
    assert card["next_action"]["type"] == "appointment_requested"
    assert card["next_action"]["appointment_id"] == appt.id
    kinds = {n["kind"] for n in client.get("/api/notifications?limit=100", headers=h).json()}
    assert {"followup_recommended", "appointment_requested"} <= kinds

    # ---- clinic confirm link (from the e-mail) → confirmed
    link = re.search(r"/api/appointments/[^\"'\s<>]+/confirm\?[^\"'\s<>]+", mail.html).group(0).replace("&amp;", "&")
    c = client.get(link)  # GET only shows the confirm page (mail scanners prefetch links)
    assert c.status_code == 200 and "Confirm this time" in c.text, c.text
    db.expire_all()
    assert db.get(Appointment, appt.id).status == "requested"
    c = client.post(link)
    assert c.status_code == 200, c.text
    db.expire_all()
    assert db.get(Appointment, appt.id).status == "confirmed"
    card = next(c for c in _cards(client, h)["pathways"] if c["pathway"] == "cardio")
    assert card["next_action"]["type"] == "appointment_confirmed"

    # ---- chat with RAG citations + extracted observation
    r = client.post("/api/chat", headers=h, json={"message": "How should I measure my home blood pressure? "
                                                             "I felt a bit dizzy yesterday.", "language": "en"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["red_flags"] == [] and b["sources"] and b["sources"][0]["id"].startswith("src_")
    assert [(o["kind"], o["label"]) for o in b["observations_created"]] == [("symptom", "dizziness")]

    # ---- red flag → emergency guidance + notification; next risk run: general=emergency, no new booking
    r = client.post("/api/chat", headers=h, json={"message": "I have crushing chest pain right now", "language": "en"})
    b = r.json()
    assert r.status_code == 200 and b["red_flags"] and b["assessment_hint"] == "emergency"
    assert "emergency" in {n["kind"] for n in client.get("/api/notifications?limit=100", headers=h).json()}
    n_appts = _count(db, Appointment, user.id)
    r = client.post("/api/risk/run", headers=h)
    rows = {a["pathway"]: a for a in r.json()["assessments"]}
    assert rows["general"]["level"] == "emergency"
    db.expire_all()
    assert _count(db, Appointment, user.id) == n_appts
    assert len([m for m in outbox.outbox if (m.tags or {}).get("kind") == "booking_request"]) == 1

    # ---- check-in questions
    r = client.get("/api/chat/checkin?language=en", headers=h)
    assert r.status_code == 200 and len(r.json()) >= 1

    # ---- story battery with FakeStt
    r = client.post("/api/tasks/story/start?language=en", headers=h)
    assert r.status_code == 201, r.text
    tid = r.json()["task_id"]
    db.expire_all()
    s1, s2 = (get_story_by_id(s) for s in db.get(AssessmentTask, tid).raw_json["story_ids"])
    texts = {"story1_recall": s1.text, "story2_recall": s2.text, "fluency_60s": "dog cat horse lion tiger",
             "story1_delayed_recall": s1.text}
    for step in STORY_STEPS:
        if step in texts:
            app.dependency_overrides[task_routes.get_stt] = lambda t=texts[step]: FakeStt(text=t)
            r = client.post(f"/api/tasks/story/{tid}/step", headers=h, data={"step": step},
                            files={"audio": ("rec.webm", b"not-really-audio", "audio/webm")})
        elif step == "delay":
            r = client.post(f"/api/tasks/story/{tid}/step", headers=h, data={"step": step, "force_delay": "true"})
        else:
            r = client.post(f"/api/tasks/story/{tid}/step", headers=h, data={"step": step, "played_s": "50"})
        assert r.status_code == 200, (step, r.text)
    assert r.json()["finished"] and r.json()["result"]["valid"]

    # ---- vowel + tapping + history
    r = client.post("/api/tasks/vowel", headers=h, data={"repetition": "1"},
                    files={"audio": ("ahh.wav", _wav(synth_vowel()), "audio/wav")})
    assert r.status_code == 201 and r.json()["valid"], r.text
    ev = [{"key": "s;"[i % 2], "down_ms": 100 + i * 480.0, "up_ms": 190 + i * 480.0} for i in range(60)]
    r = client.post("/api/tasks/tapping", headers=h, json={"input_method": "keyboard", "duration_s": 30,
                                                           "keys": ["s", ";"], "left": {"events": ev},
                                                           "right": {"events": ev}})
    assert r.status_code == 201 and r.json()["valid"], r.text
    hist = client.get("/api/tasks/history", headers=h).json()["items"]
    types = {i["task_type"] for i in hist}
    assert {"story_recall", "sustained_vowel", "finger_tap"} <= types

    # ---- export contains everything
    exp = client.get("/api/me/export", headers=h).json()
    for table in ("risk_assessments", "reports", "appointments", "chat_messages", "observations",
                  "assessment_tasks", "notifications", "bp_readings", "oauth_credentials"):
        assert exp[table], table
    assert all(x["encrypted_tokens"] == "<redacted>" for x in exp["oauth_credentials"])
    assert {t["task_type"] for t in exp["assessment_tasks"]} >= {"story_recall", "sustained_vowel", "finger_tap"}

    # ---- delete account: rows + files gone
    uid = user.id
    user_dir = Path(settings.storage_dir).resolve() / uid
    assert user_dir.exists() and any(p.is_file() for p in user_dir.rglob("*"))
    r = client.delete("/api/me", headers=h)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert r.json()["files_deleted"] >= 3
    db.expire_all()
    assert db.get(User, uid) is None
    for model in USER_OWNED_MODELS:
        assert _count(db, model, uid) == 0, model.__tablename__
    assert not user_dir.exists()
    assert client.get("/api/dashboard", headers=h).status_code == 401
