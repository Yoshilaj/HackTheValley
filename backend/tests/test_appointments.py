"""Appointment routes, approval, confirm link, cancel, the appointment_followups job and the full
followup_recommended → confirmed chain (fakes only)."""

from datetime import datetime, timedelta

import pytest

from app.core.config import settings
from app.integrations import google_calendar as gcal
from app.integrations.google_calendar import FakeCalendar
from app.providers import email as email_mod
from app.providers.email import FakeEmail
from app.providers.llm import FakeLlm
from app.services import booking_agent as ba
from app.services import followup
from app.services import notify as notify_mod
from app.services.booking.channels import ChannelResult
from tests.booking_helpers import ScriptedChannel, clinic_mail, make_assessment, notes, toronto_user  # noqa: F401
from tests.booking_helpers import no_web  # noqa: F401  (autouse)
from tests.test_report import headers_for


@pytest.fixture()
def planned(db):
    user = toronto_user(db)
    cal, email = FakeCalendar(), FakeEmail()
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, email, "en")
    assert appt.status == "awaiting_approval"
    return user, appt, cal, email


@pytest.fixture()
def booked(planned, db):
    user, appt, cal, email = planned
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)
    assert appt.status == "requested"
    return user, appt, cal, email


def _kinds(db, user):
    return [n.kind for n in notes(db, user)]


def test_confirm_service_updates_event_and_notifies(db, booked):
    user, appt, cal, _ = booked
    out = ba.confirm_appointment(db, appt.id, 1, ba.confirm_token_for(appt, 1), calendar=cal)
    assert out.status == "confirmed" and out.chosen_slot == appt.proposed_slots[1]
    # calendar writes wait for the user's OK (notification B): nothing written yet, the card says "pending"
    assert cal.events == {} and appt.calendar_event_id is None and appt.approval["calendar"]["status"] == "pending"
    assert "appointment_confirmed" in _kinds(db, user)
    ba.add_to_calendar(db, user, appt.id, calendar=cal)
    event = cal.events[appt.calendar_event_id]
    assert event["status"] == "confirmed" and event["start"].isoformat() == appt.proposed_slots[1]["start"]
    assert appt.approval["calendar"] | {"status": "added", "event_id": appt.calendar_event_id} == appt.approval["calendar"]
    assert ba.confirm_appointment(db, appt.id, 1, ba.confirm_token_for(appt, 1), calendar=cal).status == "confirmed"
    with pytest.raises(ba.AppointmentStateError):
        ba.confirm_appointment(db, appt.id, 0, ba.confirm_token_for(appt, 0), calendar=cal)
    with pytest.raises(PermissionError):  # a token for slot 1 never confirms slot 0
        ba.confirm_appointment(db, appt.id, 0, ba.confirm_token_for(appt, 1), calendar=cal)


def test_confirm_link_refused_before_approval(client, db, planned):
    _, appt, _, _ = planned
    res = client.post(f"/api/appointments/{appt.id}/confirm", params={"slot": 0, "token": ba.confirm_token_for(appt, 0)})
    assert res.status_code == 409


def test_confirm_route_public(client, db, booked):
    _, appt, _, _ = booked
    base = f"/api/appointments/{appt.id}/confirm"
    assert client.get(base, params={"slot": 0, "token": "nope"}).status_code == 403
    assert client.post(base, params={"slot": 0, "token": "nope"}).status_code == 403
    assert client.get(base, params={"slot": 5, "token": ba.confirm_token_for(appt, 5)}).status_code == 400
    assert client.get("/api/appointments/unknown/confirm", params={"slot": 0, "token": "x"}).status_code == 404
    # GET (what a mail scanner prefetches) only shows the time and a POST button
    page = client.get(base, params={"slot": 2, "token": ba.confirm_token_for(appt, 2)})
    assert page.status_code == 200 and "<form method='post'" in page.text and "Confirm this time" in page.text
    db.refresh(appt)
    assert appt.status == "requested"
    res = client.post(base, params={"slot": 2, "token": ba.confirm_token_for(appt, 2)})
    assert res.status_code == 200 and "confirmed" in res.text.lower()
    db.refresh(appt)
    assert appt.status == "confirmed" and appt.chosen_slot == appt.proposed_slots[2]
    again = client.get(base, params={"slot": 2, "token": ba.confirm_token_for(appt, 2)})
    assert again.status_code == 200 and "Already confirmed" in again.text


def test_approve_decline_routes(client, db, monkeypatch):
    fake = FakeEmail()
    monkeypatch.setattr(email_mod, "get_email_provider", lambda: fake)
    monkeypatch.setattr(notify_mod, "get_email_provider", lambda: fake)
    user = toronto_user(db)
    h, other = headers_for(user), headers_for(toronto_user(db))
    a1 = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), FakeCalendar(), None, fake, "en")
    a2 = ba.run_booking_agent(db, user, make_assessment(db, user, with_data=False), FakeLlm(), FakeCalendar(),
                              None, fake, "en")
    assert client.post(f"/api/appointments/{a1.id}/approve", headers=other).status_code == 404
    res = client.post(f"/api/appointments/{a1.id}/approve", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "requested" and body["channel"] == "email" and body["approval"]["status"] == "approved"
    assert [a["channel"] for a in body["channel_attempts"]] == ["web", "email"]
    assert "reply_token" not in body
    assert len(clinic_mail(fake.outbox)) == 1
    assert client.post(f"/api/appointments/{a1.id}/approve", headers=h).status_code == 409
    assert client.post(f"/api/appointments/{a1.id}/decline", headers=h).status_code == 409
    res = client.post(f"/api/appointments/{a2.id}/decline", headers=h)
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    assert len(clinic_mail(fake.outbox)) == 1
    assert client.post(f"/api/appointments/{a2.id}/user-info", headers=h, json={"answer": "x"}).status_code == 409


def test_approve_refused_after_consent_revoked(client, db, planned):
    user, appt, _, _ = planned
    h = headers_for(user)
    client.post("/api/consent", headers=h, json={"purpose": "share_with_clinic", "granted": False})
    db.expire_all()
    res = client.post(f"/api/appointments/{appt.id}/approve", headers=h)
    assert res.status_code == 409 and "consent" in res.json()["detail"]


def test_list_detail_cancel_routes(client, db, booked):
    user, appt, _, _ = booked
    h = headers_for(user)
    listed = client.get("/api/appointments", headers=h).json()
    assert [a["id"] for a in listed] == [appt.id] and "agent_log" not in listed[0] and "candidates" not in listed[0]
    detail = client.get(f"/api/appointments/{appt.id}", headers=h).json()
    assert detail["status"] == "requested" and len(detail["proposed_slots"]) == 3 and len(detail["candidates"]) == 5
    assert any(e.get("type") == "channel" and e.get("channel") == "email" for e in detail["agent_log"])
    other = headers_for(toronto_user(db))
    assert client.get(f"/api/appointments/{appt.id}", headers=other).status_code == 404
    assert client.post(f"/api/appointments/{appt.id}/cancel", headers=other).status_code == 404
    assert client.get(f"/api/appointments/{appt.id}/messages", headers=other).status_code == 404
    res = client.post(f"/api/appointments/{appt.id}/cancel", headers=h)
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    conf = client.post(f"/api/appointments/{appt.id}/confirm", params={"slot": 0, "token": ba.confirm_token_for(appt, 0)})
    assert conf.status_code == 409


def test_cancel_service_deletes_event_and_tells_clinic(db, booked):
    user, appt, cal, _ = booked
    email = FakeEmail()
    out = ba.cancel_appointment(db, user, appt.id, calendar=cal, email=email)
    assert out.status == "cancelled" and appt.calendar_event_id not in cal.events
    assert email.outbox[0].to == ["booking@bloorheart.example.com"]


def test_admin_run_route(client, db, admin_headers):
    user = toronto_user(db)
    assessment = make_assessment(db, user)
    assert client.post(f"/api/appointments/run/{assessment.id}").status_code == 403
    res = client.post(f"/api/appointments/run/{assessment.id}", headers=admin_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "awaiting_approval" and body["clinic"]["specialty"] == "cardiology"
    assert body["approval"]["channel_plan"][1]["channel"] == "email"
    assert client.post("/api/appointments/run/missing", headers=admin_headers).status_code == 404
    no_consent = toronto_user(db, consents=["data_processing"])
    a2 = make_assessment(db, no_consent, with_data=False)
    assert client.post(f"/api/appointments/run/{a2.id}", headers=admin_headers).status_code == 409


def _backdate(db, appt, days):
    """Move the appointment's contact history `days` into the past."""
    shift = timedelta(days=days)
    appt.created_at = appt.created_at - shift
    appt.channel_attempts = [a | {"ts": (datetime.fromisoformat(a["ts"]) - shift).isoformat()}
                             for a in appt.channel_attempts]
    db.commit()


def test_followups_reminder_then_failed(db, booked):
    user, appt, _, _ = booked
    _backdate(db, appt, 30)
    contact = datetime.fromisoformat(appt.channel_attempts[-1]["ts"])
    email = FakeEmail()
    mine = lambda: [m for m in email.outbox if (m.tags or {}).get("appointment_id") == appt.id]  # noqa: E731

    ba.process_appointment_followups(db, now=contact + timedelta(days=2), email=email, calendar=FakeCalendar())
    assert mine() == [] and appt.status == "requested"
    ba.process_appointment_followups(db, now=contact + timedelta(days=4), email=email, calendar=FakeCalendar())
    assert len(mine()) == 1 and mine()[0].subject.startswith("Reminder") and mine()[0].tags["kind"] == "booking_reminder"
    assert "/r/" in mine()[0].html and "confirm?slot=0" in mine()[0].text
    assert sum(1 for e in appt.agent_log if e["type"] == "reminder") == 1
    assert appt.channel == "email" and appt.status == "requested"
    ba.process_appointment_followups(db, now=contact + timedelta(days=8), email=email, calendar=FakeCalendar())
    assert len(mine()) == 1  # one follow-up per clinic
    ba.process_appointment_followups(db, now=contact + timedelta(days=10, minutes=1), email=email,
                                     calendar=FakeCalendar())
    db.commit()
    assert appt.status == "failed" and "appointment_failed" in _kinds(db, user)


def test_followups_try_next_channel_first(db):
    user = toronto_user(db)
    phone = ScriptedChannel("phone", result=ChannelResult(status="requested", reason="call placed",
                                                          external_ref="conv_9"))
    chans = {"web": ScriptedChannel("web", reason="captcha"),
             "email": ScriptedChannel("email", result=ChannelResult(status="requested", reason="sent",
                                                                    details={"request": {"to": "x@example.com"}})),
             "phone": phone}
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), FakeCalendar(), None, FakeEmail(),
                                "en", channels=chans)
    ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=FakeEmail(), channels=chans)
    assert appt.channel == "email" and phone.calls == []
    _backdate(db, appt, 40)
    contact = datetime.fromisoformat(appt.channel_attempts[-1]["ts"])
    counts = ba.process_appointment_followups(db, now=contact + timedelta(days=4), email=FakeEmail(),
                                              calendar=FakeCalendar(), channels=chans)
    assert counts["next_channel"] >= 1 and phone.calls == [appt.clinic["id"]]
    assert appt.channel == "phone" and appt.status == "requested" and appt.external_ref == "conv_9"
    assert appt.clinic["request"]["to"] == "x@example.com"  # the e-mail thread is kept
    assert any(e["type"] == "next_channel" for e in appt.agent_log)


def test_followups_job_registered_and_runs(db):
    from app.jobs import JOBS, run_job

    spec = JOBS["appointment_followups"]
    assert spec.cron == "0 9 * * *"
    row = run_job("appointment_followups", db)
    assert row.status == "done", row.error
    assert set(row.result) == {"checked", "reminded", "next_channel", "failed", "cancelled"}


def test_full_chain_followup_to_confirmed(client, db, monkeypatch):
    """followup_recommended → awaiting_approval → approve → requested → inbound confirmation →
    confirmed + calendar event (all HTTP except the risk hook)."""
    import json

    fake, cal = FakeEmail(), FakeCalendar()
    monkeypatch.setattr(email_mod, "get_email_provider", lambda: fake)
    monkeypatch.setattr(notify_mod, "get_email_provider", lambda: fake)
    monkeypatch.setattr(gcal, "get_calendar_client", lambda db, user_id: cal)
    monkeypatch.setattr(settings, "booking_reply_domain", "book.example.org")
    user = toronto_user(db)
    h = headers_for(user)
    _, appt = followup.handle_followup(db, user, make_assessment(db, user), llm=FakeLlm())
    assert appt.status == "awaiting_approval" and clinic_mail(fake.outbox) == []
    assert "appointment_approval_needed" in _kinds(db, user)

    res = client.post(f"/api/appointments/{appt.id}/approve", headers=h)
    assert res.status_code == 200 and res.json()["status"] == "requested"
    mail = clinic_mail(fake.outbox)[0]
    assert mail.reply_to.startswith("book+") and mail.reply_to.endswith("@book.example.org")
    assert res.json()["calendar_event_id"] is None and cal.events == {}  # no silent tentative hold

    payload = {"type": "email.received", "data": {"email_id": "in_1", "from": mail.to[0], "to": [mail.reply_to],
                                                  "subject": "Re: " + mail.subject,
                                                  "text": "Hello, option 3 works. See you then!"}}
    r = client.post("/api/webhooks/resend/inbound", content=json.dumps(payload),
                    headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json()["appointment_status"] == "confirmed", r.json()
    detail = client.get(f"/api/appointments/{appt.id}", headers=h).json()
    assert detail["status"] == "confirmed" and detail["chosen_slot"] == detail["proposed_slots"][2]
    assert "appointment_confirmed" in _kinds(db, user)
    # notification B: the user adds it to the calendar from the app
    sync = client.get(f"/api/appointments/{appt.id}/calendar-sync", headers=h).json()
    assert sync["status"] == "pending" and sync["mode"] == "google" and sync["ics_url"].endswith(f"token={sync['ics_url'].split('token=')[1]}")
    sync = client.post(f"/api/appointments/{appt.id}/calendar-sync", json={"decision": "add"}, headers=h).json()
    ev = cal.events[sync["event_id"]]
    assert sync["status"] == "added" and ev["status"] == "confirmed"
    assert ev["start"].isoformat() == detail["proposed_slots"][2]["start"]
    msgs = client.get(f"/api/appointments/{appt.id}/messages", headers=h).json()
    assert [(m["direction"], (m["classification"] or {}).get("outcome")) for m in msgs] == [
        ("out", None), ("in", "confirmed")]
