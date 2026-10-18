"""Resend inbound webhook: Svix signature, matching, classification and outcome handling (fakes only)."""

import base64
import json
import time
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import AppointmentMessage
from app.integrations import Slot
from app.integrations import google_calendar as gcal
from app.integrations.google_calendar import FakeCalendar
from app.providers import email as email_mod
from app.providers.email import FakeEmail
from app.providers.llm import FakeLlm
from app.services import booking_agent as ba
from app.services import notify as notify_mod
from app.services.booking import inbound
from tests.booking_helpers import clinic_mail, make_assessment, notes, toronto_user  # noqa: F401
from tests.booking_helpers import no_web  # noqa: F401  (autouse)
from tests.test_report import headers_for

TOR = ZoneInfo("America/Toronto")
SECRET = "whsec_" + base64.b64encode(b"test-webhook-secret-0123456789").decode()
URL = "/api/webhooks/resend/inbound"


@pytest.fixture()
def shared(monkeypatch):
    """One FakeEmail + one FakeCalendar for every code path (routes build their own clients)."""
    fake_email, cal = FakeEmail(), FakeCalendar()
    monkeypatch.setattr(email_mod, "get_email_provider", lambda: fake_email)
    monkeypatch.setattr(notify_mod, "get_email_provider", lambda: fake_email)
    monkeypatch.setattr(gcal, "get_calendar_client", lambda db, user_id: cal)
    monkeypatch.setattr(settings, "booking_reply_domain", "book.example.org")
    return fake_email, cal


@pytest.fixture()
def requested(db, shared):
    email, cal = shared
    user = toronto_user(db)
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, email, "en")
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)
    assert appt.status == "requested" and appt.reply_token
    return user, appt


def event(appt=None, text="Option 2 works for us, see you then.", sender="booking@bloorheart.example.com",
          to=None, email_id=None, **data):
    to = to if to is not None else [f"book+{appt.reply_token}@book.example.org"]
    return {"type": "email.received", "created_at": "2026-10-18T13:00:00Z",
            "data": {"email_id": email_id or str(uuid4()), "from": sender, "to": to, "cc": [], "bcc": [],
                     "subject": "Re: Appointment request", "message_id": "<x@clinic>", "attachments": [],
                     "text": text} | data}


def post(client, payload, secret=None, ts=None, tamper=False):
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    if secret:
        sid, stamp = f"msg_{uuid4().hex}", str(ts or int(time.time()))
        headers |= {"svix-id": sid, "svix-timestamp": stamp,
                    "svix-signature": inbound.sign_svix(secret, sid, stamp, body + (b" " if tamper else b""))}
    return client.post(URL, content=body, headers=headers)


# ---------------------------------------------------------------- signature

def test_svix_signature_verification(client, db, requested, monkeypatch):
    _, appt = requested
    monkeypatch.setattr(settings, "resend_webhook_secret", SECRET)
    assert post(client, event(appt)).status_code == 401                       # unsigned
    assert post(client, event(appt), secret=SECRET, tamper=True).status_code == 401
    assert post(client, event(appt), secret=SECRET, ts=int(time.time()) - 3600).status_code == 401  # replay
    other = "whsec_" + base64.b64encode(b"another-secret").decode()
    assert post(client, event(appt), secret=other).status_code == 401
    res = post(client, event(appt), secret=SECRET)
    assert res.status_code == 200 and res.json()["status"] == "processed"
    # multiple signatures (key rotation): any v1 match is accepted
    body, sid, stamp = b"{}", "msg_1", str(int(time.time()))
    good = inbound.sign_svix(SECRET, sid, stamp, body)
    inbound.verify_svix(SECRET, sid, stamp, f"v1,bm9wZQ== {good}", body)
    with pytest.raises(inbound.WebhookVerificationError):
        inbound.verify_svix(SECRET, sid, stamp, "v2," + good.split(",")[1], body)


def test_no_secret_rejected_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "resend_webhook_secret", "")
    monkeypatch.setattr(settings, "dev_mode", False)
    assert post(client, {"type": "email.received", "data": {}}).status_code == 503
    monkeypatch.setattr(settings, "dev_mode", True)
    r = post(client, {"type": "email.sent", "data": {}})
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert client.post(URL, content=b"not json").status_code == 400


# ---------------------------------------------------------------- outcomes

def test_confirmed_by_reply_token_and_idempotent(client, db, requested, shared):
    user, appt = requested
    _, cal = shared
    payload = event(appt, text="Option 2 works for us, see you then.\n\nOn Mon, X wrote:\n> Appointment request")
    r = post(client, payload)
    assert r.json() | {"status": "processed", "matched_by": "reply_token", "outcome": "confirmed"} == r.json()
    db.refresh(appt)
    assert appt.status == "confirmed" and appt.chosen_slot == appt.proposed_slots[1]
    assert cal.events == {} and appt.approval["calendar"]["status"] == "pending"  # written only on the user's OK
    ba.add_to_calendar(db, user, appt.id, calendar=cal)
    ev = cal.events[appt.calendar_event_id]
    assert ev["status"] == "confirmed" and ev["start"].isoformat() == appt.proposed_slots[1]["start"]
    assert len(notes(db, user, "appointment_confirmed")) == 1
    msgs = db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id)
                      .order_by(AppointmentMessage.created_at)).all()
    assert [m.direction for m in msgs] == ["out", "in"]
    assert msgs[1].classification["outcome"] == "confirmed" and msgs[1].classification["method"] == "heuristic"
    assert post(client, payload).json()["status"] == "duplicate"
    assert len(notes(db, user, "appointment_confirmed")) == 1
    listed = client.get(f"/api/appointments/{appt.id}/messages", headers=headers_for(user)).json()
    assert [m["direction"] for m in listed] == ["out", "in"]


def test_llm_classification_used_when_valid(db, requested, shared):
    _, appt = requested
    email, cal = shared
    slot = appt.proposed_slots[2]["start"]
    llm = FakeLlm(extract_queue=[{"outcome": "confirmed", "slot": slot, "requested_info": "",
                                  "summary": "Clinic booked the third time."}])
    res = inbound.handle_resend_event(db, event(appt, text="We'd be happy to see Alex then."), llm=llm,
                                      calendar=cal, email=email)
    assert res["outcome"] == "confirmed" and appt.chosen_slot["start"] == slot
    msg = db.scalar(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id,
                                                     AppointmentMessage.direction == "in"))
    assert msg.classification["method"] == "llm"


def _alt_start(appt, minutes=-30):
    return datetime.fromisoformat(appt.proposed_slots[0]["start"]) + timedelta(minutes=minutes)


def test_counter_offer_free_and_open_auto_accepted(client, db, requested, shared):
    user, appt = requested
    email, _ = shared
    alt = _alt_start(appt).astimezone(TOR)
    r = post(client, event(appt, text=f"Those times are taken. Could we do {alt:%Y-%m-%d %H:%M} instead?"))
    assert r.json()["outcome"] == "counter_offer" and r.json()["action"] == "counter_offer_accepted"
    db.refresh(appt)
    assert appt.status == "confirmed" and datetime.fromisoformat(appt.chosen_slot["start"]) == alt
    replies = clinic_mail(email.outbox, "booking_reply")
    assert len(replies) == 1 and replies[0].to == ["booking@bloorheart.example.com"]
    assert replies[0].reply_to == f"book+{appt.reply_token}@book.example.org"


def test_counter_offer_busy_asks_user_then_accept(client, db, requested, shared):
    user, appt = requested
    email, cal = shared
    alt = _alt_start(appt)
    cal.busy_blocks.append((alt - timedelta(minutes=15), alt + timedelta(minutes=45)))
    r = post(client, event(appt, text=f"How about {alt.astimezone(TOR):%Y-%m-%d %H:%M} instead?"))
    assert r.json()["action"] == "asked_user"
    db.refresh(appt)
    assert appt.status == "needs_user_info"
    q = appt.approval["pending_question"]
    assert q["type"] == "counter_offer" and "calendar" in q["reason"]
    n = notes(db, user, "appointment_needs_info")
    assert len(n) == 1 and "Do you accept" in n[0].body
    h = headers_for(user)
    detail = client.get(f"/api/appointments/{appt.id}", headers=h).json()
    assert detail["pending_question"]["type"] == "counter_offer"
    assert client.post(f"/api/appointments/{appt.id}/user-info", headers=h, json={}).status_code == 422
    res = client.post(f"/api/appointments/{appt.id}/user-info", headers=h, json={"accept": True})
    assert res.status_code == 200 and res.json()["status"] == "confirmed"
    assert datetime.fromisoformat(res.json()["chosen_slot"]["start"]) == alt
    assert "accepts" in clinic_mail(email.outbox, "booking_reply")[-1].text
    assert client.post(f"/api/appointments/{appt.id}/user-info", headers=h, json={"accept": True}).status_code == 409


def test_counter_offer_outside_hours_rejected_by_user(client, db, requested, shared):
    user, appt = requested
    email, _ = shared
    sunday = datetime(2026, 11, 1, 10, 0, tzinfo=TOR)  # Bloor St Heart is closed on Sundays
    post(client, event(appt, text=f"We only have {sunday:%Y-%m-%d %H:%M} left, would that work?"))
    db.refresh(appt)
    assert appt.status == "needs_user_info" and "opening hours" in appt.approval["pending_question"]["reason"]
    res = client.post(f"/api/appointments/{appt.id}/user-info", headers=headers_for(user), json={"accept": False})
    assert res.json()["status"] == "requested"
    assert "does not work" in clinic_mail(email.outbox, "booking_reply")[-1].text


def test_needs_info_then_answer_and_health_card_refused(client, db, requested, shared):
    user, appt = requested
    email, _ = shared
    r = post(client, event(appt, text="Thanks for reaching out. Does the patient have a referral from a family doctor?"))
    assert r.json()["outcome"] == "needs_info"
    db.refresh(appt)
    assert appt.status == "needs_user_info"
    assert "referral" in appt.approval["pending_question"]["question"]
    assert len(notes(db, user, "appointment_needs_info")) == 1
    h = headers_for(user)
    bad = client.post(f"/api/appointments/{appt.id}/user-info", headers=h, json={"answer": "My OHIP is 1234-567-890"})
    assert bad.status_code == 422 and "health-card" in bad.json()["detail"]
    ok = client.post(f"/api/appointments/{appt.id}/user-info", headers=h, json={"answer": "Yes, from Dr. Chen."})
    assert ok.status_code == 200 and ok.json()["status"] == "requested"
    reply = clinic_mail(email.outbox, "booking_reply")[-1]
    assert "Yes, from Dr. Chen." in reply.text and reply.to == ["booking@bloorheart.example.com"]
    assert ok.json()["approval"]["answered_questions"][0]["answer"] == "Yes, from Dr. Chen."


def test_declined_falls_through_to_next_approved_clinic(client, db, requested, shared):
    user, appt = requested
    email, _ = shared
    first, second = appt.approval["candidate_ids"][:2]
    old_token = appt.reply_token
    r = post(client, event(appt, text="Unfortunately we are not accepting new patients at this time."))
    assert r.json()["outcome"] == "declined" and r.json()["appointment_status"] == "requested"
    db.refresh(appt)
    assert appt.clinic["id"] == second != first and appt.reply_token != old_token  # no new approval needed
    assert appt.approval["active_rank"] == 2 and appt.approval["status"] == "approved"
    assert ("email", "declined") in [(a["channel"], a["status"]) for a in appt.channel_attempts]
    assert len(notes(db, user, "appointment_approval_needed")) == 1
    assert clinic_mail(email.outbox)[-1].to == [appt.clinic["email"]]


def _unique_clinic_address(db, appt):
    addr = f"desk-{uuid4().hex[:8]}@clinic.example.com"
    clinic = dict(appt.clinic)
    clinic["email"] = addr
    clinic["request"] = dict(clinic["request"]) | {"to": addr}
    clinic["requests"] = {k: (dict(v) | {"to": addr} if k == "email" else v) for k, v in clinic["requests"].items()}
    appt.clinic = clinic
    db.commit()
    return addr


def test_sender_fallback_unmatched_and_body_fetch(client, db, requested, shared, monkeypatch):
    _, appt = requested
    r = post(client, event(appt, to=["somewhere@book.example.org"], text="random note",
                           sender="Someone <nobody@else.example.com>"))
    assert r.json()["status"] == "unmatched"
    # body fetched from the Resend API — only after the reply token matched
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        return httpx.Response(200, json={"id": "e1", "html": "<p>The <b>first</b> option is confirmed.</p>",
                                         "text": None, "headers": {"from": "Bloor <booking@bloorheart.example.com>"}},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    payload = event(appt, sender="booking@bloorheart.example.com", email_id=f"e1-{uuid4().hex}")
    payload["data"].pop("text")
    r = post(client, payload)
    assert r.json()["matched_by"] == "reply_token" and r.json()["outcome"] == "confirmed", r.json()
    assert calls[0][0].startswith("https://api.resend.com/emails/receiving/e1-")
    assert calls[0][1]["Authorization"] == "Bearer re_test"
    db.refresh(appt)
    assert appt.chosen_slot == appt.proposed_slots[0]


def test_unmatched_mail_is_never_fetched(client, db, requested, monkeypatch):
    _, appt = requested
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("body fetched before matching"))
    payload = event(appt, to=["somebody@book.example.org"], sender="x@nowhere.example.com")
    payload["data"].pop("text")
    assert post(client, payload).json()["status"] == "unmatched"


def test_sender_fallback_never_changes_state(client, db, requested):
    """No reply token: a sender match never confirms / declines; a message is stored only when
    exactly one open appointment matches, and the user is asked to review it."""
    user, appt = requested
    addr = _unique_clinic_address(db, appt)
    r = post(client, event(appt, to=["intake@book.example.org"], sender=addr, text="Option 1 is confirmed."))
    body = r.json()
    assert body["status"] == "needs_review" and body["matched_by"] == "sender_review", body
    db.refresh(appt)
    assert appt.status == "requested" and appt.chosen_slot is None
    msgs = db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id,
                                                       AppointmentMessage.direction == "in")).all()
    assert [m.classification["outcome"] for m in msgs] == ["unverified"]
    assert any("please review" in n.title for n in notes(db, user, "appointment_needs_info"))


def test_sender_fallback_ambiguous_or_sandbox_is_unmatched(client, db, shared, monkeypatch):
    """Two users' open appointments with the same clinic address: the mail is stored for neither."""
    email, cal = shared
    appts = []
    for _ in range(2):
        user = toronto_user(db)
        a = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, email, "en")
        ba.approve_appointment(db, user, a.id, calendar=cal, email=email)
        appts.append(a)
    addr = _unique_clinic_address(db, appts[0])
    clinic = dict(appts[1].clinic)
    clinic["email"], clinic["request"] = addr, dict(clinic["request"]) | {"to": addr}
    appts[1].clinic = clinic
    db.commit()
    r = post(client, event(appts[0], to=["intake@book.example.org"], sender=addr, text="Option 1 is confirmed."))
    assert r.json()["status"] == "unmatched"
    # the shared sandbox inbox never matches by sender
    solo = _unique_clinic_address(db, appts[0])
    monkeypatch.setattr(settings, "sandbox_clinic_email", solo)
    r = post(client, event(appts[0], to=["intake@book.example.org"], sender=solo, text="Option 1 is confirmed."))
    assert r.json()["status"] == "unmatched"
    for a in appts:
        db.refresh(a)
        assert a.status == "requested"
        assert not db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == a.id,
                                                               AppointmentMessage.direction == "in")).all()


def test_html_to_text_large_unclosed_script_is_fast():
    html = "<p>Option 2 works.</p><script>" + "<a " * 200_000  # unclosed, ~600 KB
    t0 = time.monotonic()
    out = inbound.html_to_text(html)
    assert time.monotonic() - t0 < 1.0
    assert out == "Option 2 works."
    assert inbound.html_to_text("<p>A<br>B</p><style>x{}</style><div>C &amp; D</div>") == "A\nB\nC & D"


def test_reply_to_cancelled_appointment_ignored(client, db, requested):
    user, appt = requested
    ba.cancel_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail())
    r = post(client, event(appt, text="Option 1 is confirmed."))
    assert r.json()["action"] == "ignored"
    db.refresh(appt)
    assert appt.status == "cancelled"


# ---------------------------------------------------------------- heuristic parser

@pytest.mark.parametrize("text,outcome", [
    ("Confirmed: the 2nd time works.", "confirmed"),
    ("You're booked for slot 3.", "confirmed"),
    ("We can't book you without a referral, sorry.", "declined"),
    ("Please send a copy of the referral letter.", "needs_info"),
    ("Thanks, we received your message.", "other"),
])
def test_heuristic_classifier(text, outcome):
    base = datetime(2026, 10, 20, 10, 0, tzinfo=TOR)
    proposed = [Slot(base + timedelta(days=i), base + timedelta(days=i, minutes=30)) for i in range(3)]
    assert inbound.heuristic_classify(text, proposed, "America/Toronto")["outcome"] == outcome


def test_heuristic_month_day_time_parsing():
    base = datetime(2026, 10, 20, 10, 0, tzinfo=TOR)
    proposed = [Slot(base, base + timedelta(minutes=30))]
    r = inbound.heuristic_classify("We could offer Oct 23 at 2:30 pm instead.", proposed, "America/Toronto",
                                   now=base)
    assert r["outcome"] == "counter_offer"
    assert datetime.fromisoformat(r["slot"]) == datetime(2026, 10, 23, 14, 30, tzinfo=TOR)
    assert inbound.strip_quoted("Yes.\n> old\nOn Tue, A wrote:\nquoted") == "Yes."
    assert inbound.bare_address("Clinic <Desk@Clinic.ca>") == "desk@clinic.ca"
