"""Phone channel: ElevenLabs Agents outbound call + post-call webhook (fakes / mocked transport only)."""

import json
import time
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import AppointmentMessage
from app.integrations import Clinic, Slot
from app.integrations import google_calendar as gcal
from app.integrations.google_calendar import FakeCalendar
from app.providers import email as email_mod
from app.providers import voice as voice_mod
from app.providers.email import FakeEmail
from app.providers.llm import FakeLlm
from app.providers.voice import ElevenLabsVoice, FakeVoice
from app.services import booking_agent as ba
from app.services import notify as notify_mod
from app.services.booking.channels import phone
from app.services.booking.channels.base import BookingContext, ChannelResult, PatientInfo
from app.services.booking.channels.phone import PhoneChannel
from tests.booking_helpers import (  # noqa: F401
    RAW_HEALTH,
    ScriptedChannel,
    make_assessment,
    no_web,
    notes,
    toronto_user,
)

URL = "/api/webhooks/elevenlabs/post-call"
SECRET = "wsec_test_0123456789abcdef"
SANDBOX = "+1 (647) 555-0123"
SANDBOX_E164 = "+16475550123"
REAL = "+14165550101"   # seed_bloor_heart
BLOOR = "seed_bloor_heart"


@pytest.fixture()
def configured(monkeypatch):
    for k, v in {"elevenlabs_api_key": "test-key", "elevenlabs_agent_id": "agent_test",
                 "elevenlabs_agent_phone_number_id": "phnum_test", "sandbox_clinic_phone": SANDBOX}.items():
        monkeypatch.setattr(settings, k, v)


@pytest.fixture()
def env(monkeypatch, configured):
    """FakeVoice / FakeEmail / FakeCalendar for every code path (routes build their own clients)."""
    fake_voice, fake_email, cal = FakeVoice(), FakeEmail(), FakeCalendar()
    monkeypatch.setattr(voice_mod, "get_voice_provider", lambda: fake_voice)
    monkeypatch.setattr(email_mod, "get_email_provider", lambda: fake_email)
    monkeypatch.setattr(notify_mod, "get_email_provider", lambda: fake_email)
    monkeypatch.setattr(gcal, "get_calendar_client", lambda db, user_id: cal)
    return fake_voice, fake_email, cal


def phone_first():
    """Plan where only the phone channel can reach the clinic."""
    return {"web": ScriptedChannel("web", reason="not configured"),
            "email": ScriptedChannel("email", reason="no e-mail address"), "phone": PhoneChannel()}


@pytest.fixture()
def requested(db, env):
    fake_voice, email, cal = env
    user = toronto_user(db)
    chans = phone_first()
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, email, "en",
                                channels=chans)
    assert appt.status == "awaiting_approval"
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email, channels=chans)
    assert appt.status == "requested" and appt.channel == "phone"
    return user, appt, fake_voice.calls[-1]


def post(client, payload, secret=None, ts=None, tamper=False, header=None):
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    if secret:
        headers["ElevenLabs-Signature"] = phone.sign(secret, body + (b" " if tamper else b""), ts)
    if header is not None:
        headers["ElevenLabs-Signature"] = header
    return client.post(URL, content=body, headers=headers)


def ctx_for(clinic, slots=None, **kw):
    tz = "America/Toronto"
    slots = slots or [Slot.from_any("2026-11-03T10:00:00-05:00"), Slot.from_any("2026-11-04T14:00:00-05:00"),
                      Slot.from_any("2026-11-06T09:30:00-05:00")]
    patient = PatientInfo(name="Alex Tremblay", date_of_birth="1950-02-03", phone="+1 416-555-0199",
                          email="alex@example.org", postal_code="M5S 1X5", address="401 Bloor St W",
                          reason_category="Heart and blood-pressure check-up", preferred_slots=["x"],
                          report_url="http://localhost:8000/r/tok")
    return BookingContext(db=None, user=None, appointment=None, clinic=clinic, slots=slots, clinic_tz=tz,
                          user_tz=tz, patient=patient, report_url=patient.report_url,
                          now=datetime.fromisoformat("2026-10-19T09:00:00-04:00"), **kw)


class _Appt:
    id = "appt-1"
    external_ref = None


def clinic(phone_number=REAL):
    return Clinic(id="c1", name="Bloor Street Heart Clinic (demo)", specialty="cardiology", phone=phone_number)


# ---------------------------------------------------------------- configured / available / sandbox

def test_configured_requires_key_agent_and_number(monkeypatch):
    ch = PhoneChannel()
    for k in ("elevenlabs_api_key", "elevenlabs_agent_id", "elevenlabs_agent_phone_number_id"):
        monkeypatch.setattr(settings, k, "")
    assert not ch.configured() and ch.available(clinic(), ctx_for(clinic())) == "not configured"
    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    monkeypatch.setattr(settings, "elevenlabs_agent_id", "a")
    assert not ch.configured()
    monkeypatch.setattr(settings, "elevenlabs_agent_phone_number_id", "p")
    assert ch.configured()
    assert ch.execute(_Appt(), ctx_for(clinic())).status == "skipped"  # no SANDBOX_CLINIC_PHONE


def test_available_rules(monkeypatch, configured):
    ch = PhoneChannel()
    assert ch.available(clinic(), ctx_for(clinic())) is None
    assert ch.available(clinic(None), ctx_for(clinic(None))) == "no phone number"
    monkeypatch.setattr(settings, "sandbox_clinic_phone", "")
    assert ch.available(clinic(), ctx_for(clinic())) == "sandbox: SANDBOX_CLINIC_PHONE not set"
    monkeypatch.setattr(settings, "sandbox_clinic_phone", "12345")
    assert "not a valid E.164" in ch.available(clinic(), ctx_for(clinic()))
    monkeypatch.setattr(settings, "booking_target_mode", "live")
    assert ch.available(clinic("+44 20 7946 0000"), ctx_for(clinic())) == "clinic phone number is not a Canadian +1 number"
    assert ch.available(clinic("(416) 555-0101"), ctx_for(clinic())) is None


def test_normalize_e164():
    assert phone.normalize_e164("+1 416-555-0101") == "+14165550101"
    assert phone.normalize_e164("(416) 555-0101") == "+14165550101"
    assert phone.normalize_e164("1 416 555 0101") == "+14165550101"
    assert phone.normalize_e164("555-0101") is None and phone.normalize_e164(None) is None


def test_sandbox_never_dials_the_real_number(configured):
    fake = FakeVoice()
    res = PhoneChannel(fake).execute(_Appt(), ctx_for(clinic()))
    assert res.status == "requested" and fake.calls[0]["to_number"] == SANDBOX_E164
    assert res.target["redirected"] and res.target["real"] == REAL and "sandbox: redirected from" in res.reason
    assert all(c["to_number"] != REAL for c in fake.calls)


def test_live_mode_dials_clinic_e164(monkeypatch, configured):
    monkeypatch.setattr(settings, "booking_target_mode", "live")
    fake = FakeVoice()
    res = PhoneChannel(fake).execute(_Appt(), ctx_for(clinic("(416) 555-0101")))
    assert res.status == "requested" and fake.calls[0]["to_number"] == REAL and not res.target["redirected"]


# ---------------------------------------------------------------- execute payload

def test_execute_payload_shape_via_elevenlabs_api(configured):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"success": True, "message": "ok", "conversation_id": "conv_abc",
                                         "callSid": "CA123"})

    provider = ElevenLabsVoice("test-key", "agent_test", "phnum_test", transport=httpx.MockTransport(handler))
    appt = _Appt()
    res = PhoneChannel().execute(appt, ctx_for(clinic(), providers={"phone_caller": provider}))
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
    assert req.headers["xi-api-key"] == "test-key"
    body = json.loads(req.content)
    assert set(body) == {"agent_id", "agent_phone_number_id", "to_number", "conversation_initiation_client_data"}
    assert body | {"agent_id": "agent_test", "agent_phone_number_id": "phnum_test", "to_number": SANDBOX_E164} == body
    dv = body["conversation_initiation_client_data"]["dynamic_variables"]
    assert dv["patient_name"] == "Alex Tremblay" and dv["callback_phone"] == "+1 416-555-0199"
    assert dv["reason_category"] == "Heart and blood-pressure check-up" and dv["appointment_id"] == "appt-1"
    assert dv["clinic_name"] == "Bloor Street Heart Clinic (demo)"
    assert dv["slot_1"] == "Tuesday, November 3 at 10:00 AM" and dv["slot_1_iso"] == "2026-11-03T10:00:00-05:00"
    assert "option 2: Wednesday, November 4 at 2:00 PM" in dv["preferred_slots"]
    assert dv["today"] == "Monday, October 19, 2026"
    # identity fields from onboarding ARE passed (answered only when the clinic asks, see the prompt)
    assert dv["date_of_birth"] == "February 3, 1950" and dv["date_of_birth_iso"] == "1950-02-03"
    assert dv["patient_email"] == "alex@example.org" and dv["patient_email_spoken"] == "alex at example dot org"
    assert dv["postal_code"] == "M5S 1X5" and dv["postal_code_spoken"] == "M 5 S, 1 X 5"
    assert dv["home_address"] == "401 Bloor St W"
    assert dv["family_doctor_name"] == "" and dv["has_referral"] == ""   # missing -> empty string
    flat = json.dumps(dv)
    for leaked in ("/r/tok", *RAW_HEALTH):   # report link is never read aloud; no health data
        assert leaked not in flat
    assert all(isinstance(v, str) for v in dv.values())
    assert res.status == "requested" and res.external_ref == "conv_abc" and appt.external_ref == "conv_abc"
    assert res.messages[0].external_id == "phone:conv_abc" and res.messages[0].direction == "out"
    assert res.details["request"]["call_sid"] == "CA123"


def test_execute_api_error_is_failed(configured):
    provider = ElevenLabsVoice("k", "a", "p", transport=httpx.MockTransport(
        lambda r: httpx.Response(422, json={"detail": [{"msg": "invalid to_number"}]})))
    res = PhoneChannel(provider).execute(_Appt(), ctx_for(clinic()))
    assert res.status == "failed" and "422" in res.reason
    provider = ElevenLabsVoice("k", "a", "p", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"success": False, "message": "trial: number not verified",
                                            "conversation_id": None, "callSid": None})))
    res = PhoneChannel(provider).execute(_Appt(), ctx_for(clinic()))
    assert res.status == "failed" and "not verified" in res.reason


def test_get_conversation_endpoint():
    provider = ElevenLabsVoice("k", "a", "p", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"conversation_id": r.url.path.rsplit("/", 1)[-1], "status": "done"})))
    assert provider.get_conversation("conv_x") == {"conversation_id": "conv_x", "status": "done"}


# ---------------------------------------------------------------- signature

def test_signature_valid_invalid_missing(client, db, requested, env, monkeypatch):
    _, appt, _ = requested
    fake_voice = env[0]
    monkeypatch.setattr(settings, "elevenlabs_webhook_secret", SECRET)
    payload = fake_voice.post_call_payload(appt.external_ref, "other", summary="Will call back.")
    assert post(client, payload).status_code == 401                                   # missing
    assert post(client, payload, secret=SECRET, tamper=True).status_code == 401        # body changed
    assert post(client, payload, secret="wsec_other").status_code == 401                # wrong secret
    assert post(client, payload, secret=SECRET, ts=int(time.time()) - 3600).status_code == 401  # stale
    assert post(client, payload, header="garbage").status_code == 401
    r = post(client, payload, secret=SECRET)
    assert r.status_code == 200 and r.json()["status"] == "processed"
    body, ts = b"{}", int(time.time())
    good = phone.sign(SECRET, body, ts).split("v0=")[1]
    phone.verify_signature(SECRET, f"t={ts},v0=deadbeef,v0={good}", body)  # any v0 may match


def test_unsigned_rejected_without_dev_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_webhook_secret", "")
    monkeypatch.setattr(settings, "dev_mode", False)
    assert post(client, {"type": "post_call_transcription", "data": {}}).status_code == 503
    monkeypatch.setattr(settings, "dev_mode", True)
    r = post(client, {"type": "post_call_audio", "data": {"conversation_id": "x"}})
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert client.post(URL, content=b"not json").status_code == 400
    r = post(client, {"type": "post_call_transcription", "data": {"conversation_id": "conv_unknown"}})
    assert r.json()["status"] == "unmatched"


# ---------------------------------------------------------------- full chain + outcomes

def test_full_chain_confirmed_with_calendar_event_and_idempotent(client, db, requested, env):
    user, appt, call = requested
    fake_voice, _, cal = env
    assert call["to_number"] == SANDBOX_E164 and call["dynamic_variables"]["appointment_id"] == appt.id
    out = db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id)).all()
    assert [(m.direction, m.channel, m.external_id) for m in out] == [("out", "phone", f"phone:{appt.external_ref}")]
    assert len(notes(db, user, "appointment_requested")) == 1
    slot = appt.proposed_slots[1]
    payload = fake_voice.post_call_payload(
        appt.external_ref, "confirmed", booked_slot=slot["start"], summary="Booked option 2.",
        extra_results={"confirmation_number": "BK-778"},
        transcript=[("agent", "Hello, this is an AI assistant calling on behalf of Alex Tremblay."),
                    ("user", "Sure, option 2 works. Your reference is BK-778."), ("agent", "Thank you!")])
    r = post(client, payload)
    assert r.json() | {"status": "processed", "outcome": "confirmed", "action": "confirmed",
                       "appointment_status": "confirmed", "matched_by": "external_ref"} == r.json()
    db.refresh(appt)
    assert appt.status == "confirmed" and appt.chosen_slot["start"] == slot["start"]
    ev = cal.events[appt.calendar_event_id]
    assert ev["status"] == "confirmed" and ev["start"].isoformat() == slot["start"]
    assert len(notes(db, user, "appointment_confirmed")) == 1
    assert any("BK-778" in (e.get("text") or "") for e in appt.agent_log)
    msgs = db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id)
                      .order_by(AppointmentMessage.created_at)).all()
    assert [m.direction for m in msgs] == ["out", "in"]
    inbound = msgs[1]
    assert inbound.channel == "phone" and inbound.external_id == f"phone:{appt.external_ref}:result"
    assert "Clinic: Sure, option 2 works." in inbound.body and inbound.body.startswith("Assistant: Hello")
    assert inbound.classification["outcome"] == "confirmed" and inbound.classification["method"] == "data_collection"
    # duplicate delivery: no second message, no second notification
    r2 = post(client, payload)
    assert r2.json()["status"] == "duplicate"
    assert db.scalar(select(AppointmentMessage).where(AppointmentMessage.external_id == inbound.external_id)) is not None
    assert len(db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id)).all()) == 2
    assert len(notes(db, user, "appointment_confirmed")) == 1


def test_counter_offer_by_phone_is_never_auto_accepted(client, db, requested, env):
    """The phone agent cannot call back to accept: the user is asked (never `confirmed` silently)."""
    _, appt, _ = requested
    first = Slot.from_any(appt.proposed_slots[0])
    offered = (first.start + timedelta(hours=1)).isoformat()
    r = post(client, env[0].post_call_payload(appt.external_ref, "counter_offer", booked_slot=offered,
                                              summary="They offered an hour later."))
    db.refresh(appt)
    assert r.json()["outcome"] == "counter_offer" and r.json()["action"] == "asked_user"
    assert appt.status == "needs_user_info" and appt.approval["pending_question"]["slot"]["start"] == offered


def test_needs_info_asks_user(client, db, requested, env):
    user, appt, _ = requested
    r = post(client, env[0].post_call_payload(appt.external_ref, "needs_info",
                                              requested_info="Does the patient have a referral from a family doctor?",
                                              summary="Clinic needs referral info."))
    db.refresh(appt)
    assert r.json()["action"] == "asked_user" and appt.status == "needs_user_info"
    assert "referral" in appt.approval["pending_question"]["question"]
    assert len(notes(db, user, "appointment_needs_info")) == 1


def test_declined_moves_to_next_candidate(client, db, requested, env):
    _, appt, _ = requested
    r = post(client, env[0].post_call_payload(appt.external_ref, "declined", summary="Referral required first.",
                                              extra_results={"referral_required": True}))
    db.refresh(appt)
    assert r.json()["action"] == "next_candidate"
    assert ("phone", "declined") in [(a["channel"], a["status"]) for a in appt.channel_attempts]
    assert appt.clinic["id"] != BLOOR or appt.status == "failed"
    msg = db.scalar(select(AppointmentMessage).where(AppointmentMessage.direction == "in",
                                                AppointmentMessage.appointment_id == appt.id))
    assert msg.classification["referral_required"] is True


@pytest.mark.parametrize("outcome", ["no_answer", "voicemail"])
def test_no_answer_or_voicemail_fails_channel_then_next_candidate(client, db, requested, env, outcome):
    _, appt, _ = requested
    conv = appt.external_ref
    r = post(client, env[0].post_call_payload(conv, outcome, transcript=[("agent", "Hello, this is an AI assistant")]))
    db.refresh(appt)
    assert r.json()["outcome"] == outcome and r.json()["action"] == "next_candidate"
    att = [a for a in appt.channel_attempts if a["channel"] == "phone" and a.get("external_ref") == conv]
    assert [a["status"] for a in att] == ["requested", "failed"]
    assert appt.clinic["id"] != BLOOR or appt.status == "failed"
    assert db.scalar(select(AppointmentMessage).where(AppointmentMessage.external_id == f"phone:{conv}:result"))


def test_call_initiation_failure_webhook(client, db, requested, env):
    _, appt, _ = requested
    conv = appt.external_ref
    r = post(client, env[0].failure_payload(conv, "busy"))
    db.refresh(appt)
    assert r.json() | {"outcome": "no_answer", "action": "next_candidate"} == r.json()
    assert any(a["status"] == "failed" and a["reason"] == "line busy" for a in appt.channel_attempts)


def test_no_answer_waits_when_email_request_still_open(db, env):
    fake_voice, email, cal = env
    user = toronto_user(db)
    chans = {"web": ScriptedChannel("web", reason="not configured"),
             "email": ScriptedChannel("email", result=ChannelResult(status="requested", reason="sent",
                                                                    external_ref="em_1")),
             "phone": PhoneChannel()}
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, email, "en", channels=chans)
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email, channels=chans)
    assert appt.channel == "email"
    # follow-up job style: phone tried next for the same clinic
    from app.services.booking.orchestrator import _env, execute_plan
    execute_plan(_env(db, user, calendar=cal, email=email, channels=chans), appt, only=["phone"])
    assert appt.channel == "phone" and appt.external_ref.startswith("conv_fake_")
    res = phone.handle_post_call(db, fake_voice.post_call_payload(appt.external_ref, "no_answer", transcript=[]),
                                 calendar=cal, email=email, channels=chans)
    assert res["action"] == "waiting" and appt.status == "requested"
    assert appt.channel == "email" and appt.external_ref == "em_1"


def test_ambiguous_slot_resolved_from_transcript_with_llm(db, requested, env):
    _, appt, _ = requested
    slot = appt.proposed_slots[2]
    llm = FakeLlm(extract_queue=[{"outcome": "confirmed", "slot": slot["start"], "requested_info": "",
                                  "summary": "Booked the third option."}])
    payload = env[0].post_call_payload(appt.external_ref, "confirmed", booked_slot="sometime next week",
                                       transcript=[("agent", "Could you book one of the times?"),
                                                   ("user", "Let's do the last one you said.")])
    res = phone.handle_post_call(db, payload, llm=llm, calendar=env[2], email=env[1])
    assert res["action"] == "confirmed" and appt.chosen_slot["start"] == slot["start"]
    assert any(c["method"] == "extract" for c in llm.calls)
    msg = db.scalar(select(AppointmentMessage).where(AppointmentMessage.direction == "in",
                                                AppointmentMessage.appointment_id == appt.id))
    assert msg.classification["method"].startswith("data_collection+transcript_")


def test_option_number_and_unclear_time(db, requested, env):
    _, appt, _ = requested
    payload = env[0].post_call_payload(appt.external_ref, "confirmed", booked_slot="2")
    assert phone.handle_post_call(db, payload, calendar=env[2], email=env[1])["action"] == "confirmed"
    assert appt.chosen_slot["start"] == appt.proposed_slots[1]["start"]


def test_confirmed_without_any_time_asks_user(db, requested, env):
    _, appt, _ = requested
    payload = env[0].post_call_payload(appt.external_ref, "confirmed", booked_slot="",
                                       transcript=[("user", "Yes we can see her, we'll call back with a time.")])
    res = phone.handle_post_call(db, payload, llm=FakeLlm(extract_queue=[{"outcome": "other", "slot": "",
                                                                          "requested_info": "", "summary": ""}]),
                                 calendar=env[2], email=env[1])
    assert res["outcome"] == "needs_info" and appt.status == "needs_user_info"
    assert "unclear" in appt.approval["pending_question"]["question"]


def test_missing_outcome_classified_from_transcript(db, requested, env):
    _, appt, _ = requested
    slot = appt.proposed_slots[0]
    payload = env[0].post_call_payload(appt.external_ref, None, transcript=[("user", "Option 1 is booked, see you then.")])
    res = phone.handle_post_call(db, payload, llm=FakeLlm(extract_queue=[{}]), calendar=env[2], email=env[1])
    assert res["outcome"] == "confirmed" and appt.chosen_slot["start"] == slot["start"]


def test_other_outcome_recorded_only(client, db, requested, env):
    _, appt, _ = requested
    r = post(client, env[0].post_call_payload(appt.external_ref, "other", summary="Receptionist will call back."))
    db.refresh(appt)
    assert r.json()["action"] == "recorded" and appt.status == "requested"


def test_match_by_appointment_id_requires_known_conversation(db, requested, env):
    _, appt, _ = requested
    conv = appt.external_ref
    appt.external_ref = "something-else"
    db.commit()
    res = phone.handle_post_call(db, env[0].post_call_payload(conv, "other"), calendar=env[2], email=env[1])
    assert res["matched_by"] == "appointment_id"
    forged = env[0].post_call_payload(conv, "other")
    forged["data"]["conversation_id"] = "conv_forged"
    assert phone.handle_post_call(db, forged)["status"] == "unmatched"


def test_poll_conversation_fallback(db, requested, env):
    _, appt, _ = requested
    fake_voice = env[0]
    assert phone.poll_conversation(db, appt, fake_voice)["status"] == "pending"
    fake_voice.post_call_payload(appt.external_ref, "confirmed", booked_slot=appt.proposed_slots[0]["start"])
    res = phone.poll_conversation(db, appt, fake_voice, calendar=env[2], email=env[1])
    assert res["action"] == "confirmed" and appt.status == "confirmed"


# ---------------------------------------------------------------- setup script (mocked transport)

def test_setup_voice_agent_script_idempotent(tmp_path, monkeypatch, capsys):
    from scripts import setup_voice_agent as sva

    for k, v in {"elevenlabs_api_key": "xi-secret-key", "elevenlabs_agent_id": "", "twilio_account_sid": "AC123",
                 "twilio_auth_token": "twilio-secret", "twilio_phone_number": "+16475550100",
                 "elevenlabs_agent_phone_number_id": "", "base_url": "https://demo.example.org"}.items():
        monkeypatch.setattr(settings, k, v)
    state = {"webhooks": [], "agents": [], "numbers": []}
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["xi-api-key"] == "xi-secret-key" and req.url.host == "api.elevenlabs.io"
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        p = req.url.path
        if (req.method, p) == ("GET", "/v1/workspace/webhooks"):
            return httpx.Response(200, json={"webhooks": state["webhooks"]})
        if (req.method, p) == ("POST", "/v1/workspace/webhooks"):
            state["webhooks"].append({"webhook_id": "wh_1", "name": body["settings"]["name"], "is_disabled": False,
                                      "webhook_url": body["settings"]["webhook_url"], "auth_type": "hmac"})
            return httpx.Response(200, json={"webhook_id": "wh_1", "webhook_secret": "wsec_new_secret"})
        if (req.method, p) == ("GET", "/v1/convai/agents"):
            return httpx.Response(200, json={"agents": state["agents"], "has_more": False})
        if (req.method, p) == ("POST", "/v1/convai/agents/create"):
            state["agents"].append({"agent_id": "agent_1", "name": body["name"]})
            return httpx.Response(200, json={"agent_id": "agent_1"})
        if req.method == "PATCH" and p == "/v1/convai/agents/agent_1":
            return httpx.Response(200, json={"agent_id": "agent_1"})
        if (req.method, p) == ("GET", "/v1/convai/phone-numbers"):
            return httpx.Response(200, json=state["numbers"])
        if (req.method, p) == ("POST", "/v1/convai/phone-numbers"):
            state["numbers"].append({"phone_number_id": "phnum_1", "phone_number": body["phone_number"],
                                     "provider": "twilio", "assigned_agent": None})
            return httpx.Response(200, json={"phone_number_id": "phnum_1"})
        if req.method == "PATCH" and p == "/v1/convai/phone-numbers/phnum_1":
            state["numbers"][0]["assigned_agent"] = {"agent_id": body["agent_id"]}
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"detail": f"unexpected {req.method} {p}"})

    import pathlib

    def no_writes(*a, **kw):
        raise AssertionError("setup_voice_agent must never write files (the user fills .env by hand)")

    monkeypatch.setattr(pathlib.Path, "write_text", no_writes)
    monkeypatch.setattr(pathlib.Path, "open", no_writes)
    assert not hasattr(sva, "write_env")
    transport = httpx.MockTransport(handler)
    assert sva.main(["--import-number"], transport=transport) == 0
    out = capsys.readouterr().out
    assert "ELEVENLABS_AGENT_ID=agent_1" in out and "ELEVENLABS_AGENT_PHONE_NUMBER_ID=phnum_1" in out
    for secret in ("xi-secret-key", "twilio-secret"):
        assert secret not in out
    # the new webhook secret is shown exactly once, with the paste instruction
    assert out.count("wsec_new_secret") == 1 and "ELEVENLABS_WEBHOOK_SECRET=wsec_new_secret" in out
    assert "Paste this into backend/.env as ELEVENLABS_WEBHOOK_SECRET (it cannot be shown again)" in out

    create = next(b for m, p, b in calls if p == "/v1/convai/agents/create")
    agent = create["conversation_config"]["agent"]
    assert agent["language"] == "en" and "AI assistant" in agent["first_message"]
    assert "{{patient_name}}" in agent["first_message"]
    prompt = agent["prompt"]["prompt"]
    for must in ("{{slot_1}}", "{{callback_phone}}", "{{reason_category}}", "voicemail", "referral",
                 "confirmation", "AI assistant"):
        assert must in prompt
    assert agent["prompt"]["built_in_tools"]["voicemail_detection"]["params"]["voicemail_message"] == ""
    dc = create["platform_settings"]["data_collection"]
    assert {"outcome", "booked_slot", "requested_info", "summary"} <= set(dc)
    assert dc["outcome"]["enum"] == list(phone.OUTCOMES)
    assert create["platform_settings"]["workspace_overrides"]["webhooks"]["post_call_webhook_id"] == "wh_1"
    hook = next(b for m, p, b in calls if m == "POST" and p == "/v1/workspace/webhooks")
    assert hook["settings"] == {"auth_type": "hmac", "name": sva.WEBHOOK_NAME,
                                "webhook_url": "https://demo.example.org/api/webhooks/elevenlabs/post-call"}
    imp = next(b for m, p, b in calls if m == "POST" and p == "/v1/convai/phone-numbers")
    assert imp | {"phone_number": "+16475550100", "provider": "twilio", "sid": "AC123"} == imp

    # second run: everything reused / updated, nothing created, secret untouched
    calls.clear()
    assert sva.main(["--import-number"], transport=transport) == 0
    assert not [p for m, p, _ in calls if m == "POST"]
    assert ("PATCH", "/v1/convai/agents/agent_1") in [(m, p) for m, p, _ in calls]
    out = capsys.readouterr().out
    assert "reusing wh_1" in out and "wsec_new_secret" not in out


def test_setup_script_skips_webhook_for_localhost(monkeypatch, capsys, tmp_path):
    from scripts import setup_voice_agent as sva

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    monkeypatch.setattr(settings, "elevenlabs_agent_id", "agent_9")
    monkeypatch.setattr(settings, "base_url", "http://localhost:8000")
    seen = []
    transport = httpx.MockTransport(lambda r: seen.append(r.url.path) or httpx.Response(200, json={}))
    assert sva.main([], transport=transport) == 0
    assert seen == ["/v1/convai/agents/agent_9"] and "skipped" in capsys.readouterr().out
    assert sva.main(["--dry-run"]) == 0


# ---------------------------------------------------------------- onboarding identity fields / prompt / tags

IDENTITY_VARS = ("patient_first_name", "patient_last_name", "date_of_birth", "date_of_birth_iso", "patient_sex",
                 "patient_phone", "patient_phone_spoken", "patient_email", "patient_email_spoken", "home_address",
                 "street", "city", "province", "postal_code", "postal_code_spoken", "family_doctor_name",
                 "family_doctor_clinic", "family_doctor_phone", "has_referral", "preferred_language")


def test_identity_fields_from_profile_are_passed_and_health_data_is_not(db, env):
    """Real path: profile (onboarding + health data) -> patient_info -> approval -> call variables."""
    from datetime import date

    fake_voice, email, cal = env
    user = toronto_user(db, first_name="Robert", last_name="MacLeod", sex="male", date_of_birth=date(1958, 1, 15),
                        street="100 Queen St W", city="Toronto", postal_code="M5H 2N2", phone="+1-416-555-0142",
                        contact_email="r.macleod@example.com", has_referral=False,
                        family_doctor={"name": "Dr. Anita Shah", "clinic": "Kensington Family Health Team",
                                       "phone": "+1 416-555-0106"},
                        medications=[{"name": "Amlodipine", "dose": "5 mg"}], known_af=True, known_hypertension=True,
                        family_history={"stroke": True}, ldl_mg_dl=150.8, weight_kg=70.0)
    chans = phone_first()
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, email, "en", channels=chans)
    assert appt.approval["missing_profile_fields"] == []
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email, channels=chans)
    dv = fake_voice.calls[-1]["dynamic_variables"]
    assert set(IDENTITY_VARS) <= set(dv)
    assert dv["patient_name"] == "Robert MacLeod" and dv["patient_first_name"] == "Robert"
    assert dv["date_of_birth"] == "January 15, 1958" and dv["date_of_birth_iso"] == "1958-01-15"
    assert dv["patient_email"] == "r.macleod@example.com"
    assert dv["patient_email_spoken"] == "r dot macleod at example dot com"
    assert dv["home_address"] == "100 Queen St W, Toronto, ON M5H 2N2" and dv["postal_code_spoken"] == "M 5 H, 2 N 2"
    assert dv["patient_sex"] == "male" and dv["province"] == "ON" and dv["preferred_language"] == "English"
    assert dv["family_doctor_name"] == "Dr. Anita Shah" and dv["family_doctor_phone"] == "+1 416-555-0106"
    assert dv["family_doctor_clinic"] == "Kensington Family Health Team" and dv["has_referral"] == "no"
    flat = json.dumps(dv).lower()
    for leaked in (*RAW_HEALTH, "amlodipine", "150.8", "hypertension", "stroke", "medication", "known_af",
                   "health_card", "ohip", "/r/"):
        assert leaked.lower() not in flat, leaked
    assert all(isinstance(v, str) for v in dv.values())


def test_missing_identity_fields_are_empty_strings(configured):
    fake = FakeVoice()
    ctx = ctx_for(clinic())
    ctx.patient = PatientInfo(name="Alex Tremblay", reason_category="General check-up")
    PhoneChannel(fake).execute(_Appt(), ctx)
    dv = fake.calls[0]["dynamic_variables"]
    assert all(dv[k] == "" for k in IDENTITY_VARS)
    assert dv["callback_phone"] == "not provided"


def test_no_health_card_field_anywhere():
    from app.db.models import Profile
    from app.schemas.profile import ProfileUpdate
    from app.services.booking.channels.base import ALLOWED_FIELDS

    names = set(ProfileUpdate.model_fields) | {c.key for c in Profile.__table__.columns} | set(ALLOWED_FIELDS)
    names |= set(PatientInfo.__dataclass_fields__)
    assert not [n for n in names if any(w in n.lower() for w in ("health_card", "ohip", "hcn", "government",
                                                                  "insurance", "sin_"))]


def test_prompt_answers_identity_only_when_asked():
    from scripts import setup_voice_agent as sva

    p = sva.SYSTEM_PROMPT
    assert "answer ONLY when the clinic asks (never volunteer them)" in p
    assert "Never invent" in p and "health card" in p and "bring their health card" in p
    assert "Never discuss health details beyond the generic reason category" in p
    for var in IDENTITY_VARS:
        assert "{{" + var + "}}" in p, var
        assert var in sva.PLACEHOLDERS, var
    assert "spell it clearly" in p


def test_audio_tags_stripped_from_stored_transcript(db, requested, env):
    user, appt, call = requested
    fake_voice = env[0]
    payload = fake_voice.post_call_payload(appt.external_ref, "other")
    payload["data"]["transcript"] = [
        {"role": "agent", "message": "[happy] Hello, this is an AI assistant. [slow] The postal code is M 5 S."},
        {"role": "user", "message": "Okay [inaudible] thanks."},
        {"role": "agent", "message": "[laughs softly] Thank you!"}]
    phone.handle_post_call(db, payload, llm=FakeLlm())
    msg = db.scalar(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id,
                                                     AppointmentMessage.direction == "in"))
    assert "Assistant: Hello, this is an AI assistant. The postal code is M 5 S." in msg.body
    assert "Assistant: Thank you!" in msg.body and "[happy]" not in msg.body and "[slow]" not in msg.body
    assert "Clinic: Okay [inaudible] thanks." in msg.body   # the clinic's words are not rewritten
