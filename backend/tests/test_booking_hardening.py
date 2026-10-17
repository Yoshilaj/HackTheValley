"""Booking agent hardening (review round 2): counter-offers only confirmed after the clinic was
told, unapproved "confirmed" times, atomic approve / answer, clinic time zones, no web reminders,
the approved card is the execution contract, health-card regex (fakes only, no network)."""

import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.db.models import Appointment, AppointmentMessage, User
from app.integrations import Clinic, Slot
from app.integrations.google_calendar import FakeCalendar
from app.integrations.google_places import to_clinic
from app.integrations.timezones import infer_timezone, province_from_address, resolve_timezone
from app.providers.email import FakeEmail
from app.providers.llm import FakeLlm
from app.services import booking_agent as ba
from app.services.booking import orchestrator
from app.services.booking.channels import ChannelResult
from app.services.booking.slots import plan_slots
from tests.booking_helpers import ScriptedChannel, clinic_mail, make_assessment, notes, toronto_user  # noqa: F401
from tests.booking_helpers import no_web  # noqa: F401  (autouse: the web channel has its own tests)
from tests.test_booking_inbound import event, shared  # noqa: F401
from tests.test_booking_inbound import post as post_mail
from tests.test_booking_phone import configured, env, phone_first, requested  # noqa: F401
from tests.test_booking_phone import post as post_call
from tests.test_report import headers_for

TOR = ZoneInfo("America/Toronto")


@pytest.fixture()
def mail_requested(db, shared):  # noqa: F811
    """An e-mail request (reply domain set) to the first approved clinic."""
    email, cal = shared
    user = toronto_user(db)
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, email, "en")
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)
    assert appt.status == "requested" and appt.reply_token and appt.channel == "email"
    return user, appt


def _outs(db, appt):
    return db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id,
                                                       AppointmentMessage.direction == "out")).all()


# ---------------------------------------------------------------- 1: phone counter-offer

def test_phone_counter_offer_never_confirmed_without_telling_clinic(client, db, requested, env):
    user, appt, _ = requested
    voice, email, cal = env
    n_calls, n_out = len(voice.calls), len(_outs(db, appt))
    offered = (Slot.from_any(appt.proposed_slots[0]).start + timedelta(hours=1)).isoformat()
    r = post_call(client, voice.post_call_payload(appt.external_ref, "counter_offer", booked_slot=offered,
                                                  summary="They offered an hour later."))
    assert r.json()["action"] == "asked_user", r.json()
    db.refresh(appt)
    assert appt.status == "needs_user_info" and appt.chosen_slot is None
    q = appt.approval["pending_question"]
    assert q["type"] == "counter_offer" and q["reply_path"] is None and q["channel"] == "phone"
    assert "call" in q["question"].lower() and appt.clinic["name"] in q["question"]
    assert not [e for e in appt.agent_log if e["type"] == "confirmed"]
    assert len(voice.calls) == n_calls and len(_outs(db, appt)) == n_out  # nothing sent to the clinic
    # the user called the clinic back and booked it themselves -> confirmed (recorded as such)
    res = client.post(f"/api/appointments/{appt.id}/user-info", headers=headers_for(user), json={"accept": True})
    assert res.status_code == 200 and res.json()["status"] == "confirmed"
    assert res.json()["chosen_slot"]["start"] == offered
    assert any(e.get("via") == "counter_offer_booked_by_patient" for e in res.json()["agent_log"])


def test_phone_counter_offer_declined_by_user_stays_requested(client, db, requested, env):
    user, appt, _ = requested
    voice = env[0]
    offered = (Slot.from_any(appt.proposed_slots[0]).start + timedelta(hours=1)).isoformat()
    post_call(client, voice.post_call_payload(appt.external_ref, "counter_offer", booked_slot=offered))
    res = client.post(f"/api/appointments/{appt.id}/user-info", headers=headers_for(user), json={"accept": False})
    assert res.json()["status"] == "requested" and res.json()["chosen_slot"] is None


# ---------------------------------------------------------------- 1: e-mail counter-offer

def _alt(appt, minutes=-30):
    return (datetime.fromisoformat(appt.proposed_slots[0]["start"]) + timedelta(minutes=minutes)).astimezone(TOR)


def test_email_counter_offer_replied_by_email_after_followup_switched_to_phone(client, db, mail_requested, shared):
    user, appt = mail_requested
    email, _ = shared
    clinic = dict(appt.clinic)  # a follow-up placed a call: appt.channel is phone, the e-mail thread stays
    clinic["requests"] = dict(clinic["requests"]) | {"phone": {"channel": "phone", "to": "+16475550123"}}
    appt.clinic, appt.channel = clinic, "phone"
    db.commit()
    alt = _alt(appt)
    r = post_mail(client, event(appt, text=f"Those times are taken. Could we do {alt:%Y-%m-%d %H:%M} instead?"))
    assert r.json()["action"] == "counter_offer_accepted", r.json()
    replies = clinic_mail(email.outbox, "booking_reply")
    assert len(replies) == 1 and replies[0].to == [appt.clinic["email"]]
    db.refresh(appt)
    assert appt.status == "confirmed"


def test_email_counter_offer_not_confirmed_when_reply_fails(client, db, mail_requested, shared, monkeypatch):
    user, appt = mail_requested
    from app.services.booking.channels import email as email_channel

    monkeypatch.setattr(email_channel, "send_followup", lambda *a, **k: (False, None, "provider down"))
    alt = _alt(appt)
    r = post_mail(client, event(appt, text=f"Could we do {alt:%Y-%m-%d %H:%M} instead?"))
    assert r.json()["action"] == "asked_user"
    db.refresh(appt)
    assert appt.status == "needs_user_info" and appt.chosen_slot is None
    assert "could not be sent" in appt.approval["pending_question"]["reason"]
    # the user accepts, but the reply still fails -> still not confirmed
    res = client.post(f"/api/appointments/{appt.id}/user-info", headers=headers_for(user), json={"accept": True})
    assert res.status_code == 200 and res.json()["status"] == "needs_user_info"
    assert res.json()["pending_question"]["send_failed"] is True


# ---------------------------------------------------------------- 2: "confirmed" at an unapproved time

def test_phone_confirmed_arbitrary_slot_is_a_counter_offer(client, db, requested, env):
    _, appt, _ = requested
    voice, _, cal = env
    weird = (Slot.from_any(appt.proposed_slots[0]).start + timedelta(days=5, hours=13)).isoformat()
    r = post_call(client, voice.post_call_payload(appt.external_ref, "confirmed", booked_slot=weird))
    assert r.json()["action"] == "asked_user"
    db.refresh(appt)
    assert appt.status == "needs_user_info" and appt.chosen_slot is None
    assert appt.approval["pending_question"]["type"] == "counter_offer"
    assert all(ev["start"].isoformat() != weird for ev in cal.events.values())  # no calendar write


def test_phone_confirmed_within_tolerance_uses_the_approved_slot(client, db, requested, env):
    _, appt, _ = requested
    voice = env[0]
    near = (Slot.from_any(appt.proposed_slots[1]).start + timedelta(minutes=3)).isoformat()
    post_call(client, voice.post_call_payload(appt.external_ref, "confirmed", booked_slot=near))
    db.refresh(appt)
    assert appt.status == "confirmed" and appt.chosen_slot == appt.proposed_slots[1]


def test_email_confirmed_unapproved_time_outside_hours_asks_user(client, db, mail_requested, shared):
    _, appt = mail_requested
    email, _ = shared
    sunday = datetime(2026, 11, 1, 10, 0, tzinfo=TOR)  # closed on Sundays; not one of the proposals
    r = post_mail(client, event(appt, text=f"Confirmed: you're booked for {sunday:%Y-%m-%d %H:%M}."))
    assert r.json()["outcome"] == "confirmed" and r.json()["action"] == "asked_user"
    db.refresh(appt)
    assert appt.status == "needs_user_info" and appt.chosen_slot is None
    assert clinic_mail(email.outbox, "booking_reply") == []


# ---------------------------------------------------------------- 3: confirm link GET/POST + binding

def test_confirm_get_never_changes_state_and_old_clinic_links_die(client, db, mail_requested, shared):
    _, appt = mail_requested
    first_clinic = appt.clinic["id"]
    link = f"/api/appointments/{appt.id}/confirm"
    tok = ba.confirm_token_for(appt, 0)
    for _ in range(3):  # a mail scanner prefetching the link
        assert client.get(link, params={"slot": 0, "token": tok}).status_code == 200
    db.refresh(appt)
    assert appt.status == "requested"
    post_mail(client, event(appt, text="Unfortunately we are not accepting new patients at this time."))
    db.refresh(appt)
    assert appt.clinic["id"] != first_clinic and appt.status == "requested"
    assert client.post(link, params={"slot": 0, "token": tok}).status_code == 403  # earlier clinic's link
    assert client.post(link, params={"slot": 0, "token": ba.confirm_token_for(appt, 0)}).status_code == 200


# ---------------------------------------------------------------- 5: atomic approve / answer

def _plan_with(db, chans):
    user = toronto_user(db)
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), FakeCalendar(), None, FakeEmail(),
                                "en", channels=chans)
    assert appt.status == "awaiting_approval"
    return user, appt


def _chans(email_result=None, delay=0.0):
    class Slow(ScriptedChannel):
        def execute(self, appt, ctx):
            time.sleep(delay)
            return super().execute(appt, ctx)

    return {"web": ScriptedChannel("web", reason="not configured"),
            "email": Slow("email", result=email_result or ChannelResult(
                status="requested", reason="sent", details={"request": {"to": "x@example.com"}})),
            "phone": ScriptedChannel("phone", reason="not configured")}


def test_second_approve_with_stale_state_is_409_and_runs_nothing(db):
    chans = _chans()
    user, appt = _plan_with(db, chans)
    other = db_session.SessionLocal()
    try:
        stale = other.get(Appointment, appt.id)
        other_user = other.get(User, user.id)
        assert stale.status == "awaiting_approval"
        ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail(), llm=FakeLlm(),
                               channels=chans)
        assert appt.status == "requested" and len(chans["email"].calls) == 1
        assert stale.status == "awaiting_approval"  # the second request still sees the old state
        with pytest.raises(ba.AppointmentStateError):
            ba.approve_appointment(other, other_user, appt.id, calendar=FakeCalendar(), email=FakeEmail(),
                                   llm=FakeLlm(), channels=chans)
        assert len(chans["email"].calls) == 1
    finally:
        other.close()
    db.refresh(appt)
    assert appt.status == "requested"


def test_concurrent_approve_threads_run_the_plan_once(db):
    chans = _chans(delay=0.3)
    user, appt = _plan_with(db, chans)
    outcomes: list[str] = []

    def worker():
        s = db_session.SessionLocal()
        try:
            ba.approve_appointment(s, s.get(User, user.id), appt.id, calendar=FakeCalendar(), email=FakeEmail(),
                                   llm=FakeLlm(), channels=chans)
            outcomes.append("ok")
        except ba.AppointmentStateError:
            outcomes.append("409")
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(outcomes) == ["409", "ok"] and len(chans["email"].calls) == 1
    db.expire_all()
    assert db.get(Appointment, appt.id).status == "requested"


def test_second_user_info_with_stale_state_is_409(client, db, mail_requested, shared):
    user, appt = mail_requested
    email, _ = shared
    post_mail(client, event(appt, text="Does the patient have a referral from a family doctor?"))
    db.refresh(appt)
    assert appt.status == "needs_user_info"
    other = db_session.SessionLocal()
    try:
        stale, other_user = other.get(Appointment, appt.id), other.get(User, user.id)
        ba.submit_user_info(db, user, appt.id, answer="Yes, from Dr. Chen.", email=email)
        assert stale.status == "needs_user_info"  # the second request still sees the old state
        with pytest.raises(ba.AppointmentStateError):
            ba.submit_user_info(other, other_user, appt.id, answer="Yes, from Dr. Chen.", email=email)
        assert stale.status == "requested"  # refreshed after the lost claim
    finally:
        other.close()
    assert len(clinic_mail(email.outbox, "booking_reply")) == 1


def test_failed_execution_releases_the_claim(db, monkeypatch):
    chans = _chans()
    user, appt = _plan_with(db, chans)
    monkeypatch.setattr(orchestrator, "execute_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail(), llm=FakeLlm(),
                               channels=chans)
    db.refresh(appt)
    assert appt.status == "awaiting_approval"


# ---------------------------------------------------------------- 6: clinic time zones

@pytest.mark.parametrize("address,tz", [
    ("1081 Burrard St, Vancouver, BC V6Z 1Y6, Canada", "America/Vancouver"),
    ("1403 29 St NW, Calgary, AB T2N 2T9, Canada", "America/Edmonton"),
    ("1945 Hamilton St, Regina, SK S4P 2C8, Canada", "America/Regina"),
    ("820 Sherbrook St, Winnipeg, MB R3A 1R9, Canada", "America/Winnipeg"),
    ("1796 Summer St, Halifax, NS B3H 3A7, Canada", "America/Halifax"),
    ("300 Prince Philip Dr, St. John's, NL A1B 3V6, Canada", "America/St_Johns"),
    ("1000 Rue Saint-Denis, Montréal, QC H2X 0C1, Canada", "America/Toronto"),
    ("5 Hospital Rd, Whitehorse, YT Y1A 3H7, Canada", "America/Whitehorse"),
    ("Somewhere in British Columbia", "America/Vancouver"),
    ("unit 4, postal code T5J 3N4", "America/Edmonton"),
])
def test_province_to_timezone(address, tz):
    assert infer_timezone(address) == tz


def test_offset_only_and_mismatch():
    july = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    assert infer_timezone(None, -420, now=july) == "America/Vancouver"
    assert infer_timezone(None, -240, now=july) == "America/Toronto"
    # NW Ontario on Central time: the offset wins over the province default
    assert infer_timezone("Kenora, ON P9N 3W7, Canada", -300, now=july) == "America/Winnipeg"
    assert infer_timezone("Toronto, ON M5S 1X5, Canada", -240, now=july) == "America/Toronto"
    assert infer_timezone(None, None) is None and province_from_address("") is None


def test_places_result_sets_timezone_and_resolve_fallback():
    c = to_clinic({"id": "abc", "displayName": {"text": "Burrard Clinic"},
                   "formattedAddress": "1081 Burrard St, Vancouver, BC V6Z 1Y6, Canada", "utcOffsetMinutes": -420},
                  "cardiology")
    assert c.timezone == "America/Vancouver"
    assert resolve_timezone({"timezone": None, "address": "Halifax, NS B3H 3A7"}, "America/Toronto") == \
        "America/Halifax"
    assert resolve_timezone({"timezone": "Not/AZone"}, "America/Edmonton") == "America/Edmonton"
    assert resolve_timezone(None, None) == "America/Toronto"


def test_slots_for_a_vancouver_clinic_are_in_vancouver_hours():
    clinic = Clinic(id="v1", name="Burrard Clinic", specialty="cardiology",
                    address="1081 Burrard St, Vancouver, BC V6Z 1Y6, Canada",
                    opening_hours={k: [["09:00", "17:00"]] for k in ("mon", "tue", "wed", "thu", "fri")})
    plan = plan_slots(clinic, now=datetime(2026, 10, 19, 12, tzinfo=timezone.utc), fallback_tz="America/Toronto")
    assert plan.clinic_tz == "America/Vancouver" and len(plan.proposals) == 3
    van = ZoneInfo("America/Vancouver")
    for s in plan.available:
        assert 9 <= s.start.astimezone(van).hour < 17


# ---------------------------------------------------------------- 7: no web-form reminders

def test_followup_never_resubmits_the_web_form(db):
    from tests.test_appointments import _backdate

    web = ScriptedChannel("web", result=ChannelResult(status="requested", reason="form submitted",
                                                      details={"request": {"channel": "web", "to": "http://x/book"}}))
    chans = {"web": web, "email": ScriptedChannel("email", reason="no e-mail address"),
             "phone": ScriptedChannel("phone", reason="not configured")}
    user, appt = _plan_with(db, chans)
    ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail(), llm=FakeLlm(),
                           channels=chans)
    assert appt.status == "requested" and appt.channel == "web" and len(web.calls) == 1
    _backdate(db, appt, 5)
    contact = datetime.fromisoformat(appt.channel_attempts[-1]["ts"])
    ba.process_appointment_followups(db, now=contact + timedelta(days=4), email=FakeEmail(),
                                     calendar=FakeCalendar(), channels=chans)
    assert len(web.calls) == 1  # never re-submitted
    assert any(e["type"] == "reminder" and e["channel"] == "web" and e["accepted"] is False for e in appt.agent_log)
    assert any((n.payload or {}).get("reason") == "no_reply_web_form" for n in notes(db, user, "appointment_failed"))


# ---------------------------------------------------------------- 11: health-card regex

@pytest.mark.parametrize("text", ["Call me at 416-555-0199", "my cell is 4165550199", "+1 (416) 555-0199",
                                  "1-800-555-0199", "Yes, from Dr. Chen at 647 555 0123", "on 2026-11-03 10:00"])
def test_phone_numbers_are_not_health_cards(text):
    assert not orchestrator.HEALTH_CARD_RE.search(text)


@pytest.mark.parametrize("text", ["1234-567-890", "1234 567 890 AB", "1234567890XY", "my OHIP number",
                                  "health card please", "HCN 0123456789", "0123456789"])
def test_health_card_numbers_flagged(text):
    assert orchestrator.HEALTH_CARD_RE.search(text)


# ---------------------------------------------------------------- 12: the approved card is the contract

def test_backup_clinic_with_expired_times_asks_for_reapproval(db, mail_requested, shared):
    user, appt = mail_requested
    email, _ = shared
    backup = appt.approval["candidates"][1]
    first_start = Slot.from_any(backup["slots"][0]).start
    n_mail = len(clinic_mail(email.outbox))
    later = first_start - timedelta(hours=2)  # the approved backup times are < 24 h away now
    ba.apply_outcome(db, appt, {"outcome": "declined", "summary": "not accepting"}, channel="email", email=email,
                     calendar=FakeCalendar(), now=later)
    db.commit()
    assert appt.status == "awaiting_approval" and appt.approval["status"] == "pending"
    assert appt.clinic["id"] == backup["clinic_id"]
    assert all(Slot.from_any(s).start >= later + timedelta(hours=24) for s in appt.proposed_slots)
    assert len(clinic_mail(email.outbox)) == n_mail  # nothing sent with unapproved times
    assert any("Approve new times" in n.title for n in notes(db, user, "appointment_approval_needed"))


def test_execution_limited_to_the_approved_channel_plan(db):
    chans = _chans(email_result=ChannelResult.failed("bounced"))
    user, appt = _plan_with(db, chans)
    assert [p["channel"] for p in appt.approval["channel_plan"] if p["available"]] == ["email"]
    phone = ScriptedChannel("phone", result=ChannelResult(status="requested", reason="call placed"))
    run_chans = dict(chans) | {"phone": phone}  # phone became available after the card was shown
    ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail(), llm=FakeLlm(),
                           channels=run_chans)
    assert phone.calls == []
    assert any(a["channel"] == "phone" and a["reason"] == "not on the approved channel plan"
               for a in appt.channel_attempts)


# ---------------------------------------------------------------- clinic messages after confirmation

def _confirm(db, appt, email):
    first = appt.proposed_slots[0]["start"]
    res = ba.apply_outcome(db, appt, {"outcome": "confirmed", "slot": first}, channel="email", email=email)
    assert res["action"] == "confirmed" and appt.status == "confirmed"
    return appt.chosen_slot


def test_question_after_confirmation_asks_user_and_restores_confirmed(client, db, mail_requested, shared):
    user, appt = mail_requested
    email, _ = shared
    booked = _confirm(db, appt, email)
    res = ba.apply_outcome(db, appt, {"outcome": "needs_info", "requested_info": "Please bring your referral letter."},
                           channel="email", email=email)
    assert res["action"] == "asked_user" and appt.status == "needs_user_info"
    q = appt.approval["pending_question"]
    assert q["after_confirmation"] is True and q["previous_slot"] == booked
    assert notes(db, user, "appointment_needs_info")
    db.commit()
    r = client.post(f"/api/appointments/{appt.id}/user-info", headers=headers_for(user),
                    json={"answer": "I will bring it."})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert r.json()["chosen_slot"] == booked


def test_new_time_after_confirmation_is_never_auto_accepted(client, db, mail_requested, shared):
    user, appt = mail_requested
    email, _ = shared
    booked = _confirm(db, appt, email)
    n_out = len(_outs(db, appt))
    later = (Slot.from_any(appt.proposed_slots[1]).start).isoformat()
    res = ba.apply_outcome(db, appt, {"outcome": "counter_offer", "slot": later}, channel="email", email=email)
    assert res["action"] == "asked_user" and appt.status == "needs_user_info"
    assert appt.chosen_slot == booked and len(_outs(db, appt)) == n_out  # nothing accepted or sent
    db.commit()
    r = client.post(f"/api/appointments/{appt.id}/user-info", headers=headers_for(user), json={"accept": False})
    assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["chosen_slot"] == booked


def test_cancellation_text_after_confirmation_only_notifies(db, mail_requested, shared):
    user, appt = mail_requested
    email, _ = shared
    booked = _confirm(db, appt, email)
    res = ba.apply_outcome(db, appt, {"outcome": "declined", "summary": "We have to cancel."}, channel="email",
                           email=email)
    assert res["action"] == "notified_user"
    assert appt.status == "confirmed" and appt.chosen_slot == booked
    assert any("confirmed appointment" in n.title for n in notes(db, user, "appointment_needs_info"))
    again = ba.apply_outcome(db, appt, {"outcome": "confirmed", "slot": booked["start"]}, channel="email", email=email)
    assert again["action"] == "ignored"
