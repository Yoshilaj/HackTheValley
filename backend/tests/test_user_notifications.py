"""Notifications A and B of the booking flow, as the USER experiences them:

A. a sign is detected → one approval e-mail with the finding, the clinic, the times, what is shared and
   Approve / Not now buttons that work from the inbox (signed links; GET never changes state);
B. the clinic confirms → one confirmation e-mail (.ics attached) that asks before anything is written
   to the calendar; "Add to Google Calendar" writes the event only after that tap (link or in-app).
Fakes only."""

import re

import pytest

from app.core.config import settings
from app.integrations import google_calendar as gcal
from app.integrations.google_calendar import FakeCalendar
from app.providers import email as email_mod
from app.providers.email import FakeEmail
from app.providers.llm import FakeLlm
from app.services import booking_agent as ba
from app.services import notify as notify_mod
from app.services import risk_service
from app.services.booking.common import user_action_token
from tests.booking_helpers import RAW_HEALTH, clinic_mail, make_assessment, notes, toronto_user  # noqa: F401
from tests.booking_helpers import no_web  # noqa: F401  (autouse)
from tests.test_report import headers_for


@pytest.fixture()
def fakes(monkeypatch):
    """Routes build their own clients: pin them to one FakeEmail + one connected FakeCalendar."""
    fake_email, cal = FakeEmail(), FakeCalendar()
    monkeypatch.setattr(email_mod, "get_email_provider", lambda: fake_email)
    monkeypatch.setattr(notify_mod, "get_email_provider", lambda: fake_email)
    monkeypatch.setattr(gcal, "get_calendar_client", lambda db, user_id: cal)
    return fake_email, cal


@pytest.fixture()
def planned(db, fakes):
    email, cal = fakes
    user = toronto_user(db, first_name="Alex", last_name="Tremblay")
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, email, "en")
    assert appt.status == "awaiting_approval"
    return user, appt


def user_mail(outbox, kind):
    return [m for m in outbox if (m.tags or {}).get("kind") == kind]


def link(text, action):
    m = re.search(rf"/api/appointments/[\w-]+/action\?do={action}&token=([0-9]+\.[0-9a-f]+)", text)
    assert m, f"no {action} link in e-mail"
    return m.group(1)


# ---------------------------------------------------------------- A. approval e-mail

def test_approval_email_has_finding_clinic_times_and_working_buttons(db, planned, fakes):
    email, _ = fakes
    user, appt = planned
    mails = user_mail(email.outbox, "appointment_approval_needed")
    assert len(mails) == 1
    msg = mails[0]
    assert msg.to == [user.email] and msg.subject == f"Please approve: appointment request with {appt.clinic['name']}"
    body = msg.html + msg.text
    # the finding, in the user's words (structured data from the risk engine)
    finding = appt.approval["finding"]
    assert finding["pathway"] == "cardio" and finding["pathway_name"] == "Heart & blood pressure"
    assert "7-day home blood pressure averaged 146/92" in msg.text and "Hi Alex," in msg.text
    # the clinic and the three times in the user's zone
    assert appt.clinic["name"] in body and appt.clinic["address"] in body
    for row in appt.approval["slots"]:
        assert row["label_user"] in msg.text
    # what the clinic receives, and the promise
    assert "Alex Tremblay" in msg.text and "1958-01-15" in msg.text and "Never shared" in msg.text
    assert "Nothing is sent until you approve" in msg.text
    assert "Demo mode" in msg.text  # sandbox banner
    # two buttons with valid signed links + the deep link into the app
    for action in ("approve", "decline"):
        tok = link(msg.text, action)
        assert ba.verify_user_action_token(appt.id, action, appt.approval["requested_at"], tok)
    assert f"/appointments?appointment={appt.id}" in msg.text
    # the e-mail never carries raw health data beyond the finding summaries
    for raw in ("Amlodipine", "AFib", "possible_af"):
        assert raw not in body, raw
    # the in-app notification payload carries the same finding
    n = notes(db, user, "appointment_approval_needed")[0]
    assert n.payload["finding"]["pathway_name"] == "Heart & blood pressure" and n.payload["email"]["sent"]


def test_approve_link_get_is_read_only_and_post_approves(client, db, planned, fakes):
    email, cal = fakes
    user, appt = planned
    tok = link(user_mail(email.outbox, "appointment_approval_needed")[0].text, "approve")
    url = f"/api/appointments/{appt.id}/action"
    page = client.get(url, params={"do": "approve", "token": tok})
    assert page.status_code == 200 and "Approve and contact the clinic" in page.text
    assert appt.clinic["name"] in page.text and "Alex Tremblay" in page.text
    db.refresh(appt)
    assert appt.status == "awaiting_approval" and clinic_mail(email.outbox) == []  # GET changed nothing

    done = client.post(url, params={"do": "approve", "token": tok})
    assert done.status_code == 200 and "request approved" in done.text and "contacting" in done.text
    db.refresh(appt)
    assert appt.status == "requested" and len(clinic_mail(email.outbox)) == 1
    assert appt.approval["approved_by"] == "user:email_link"
    assert [e for e in appt.agent_log if e["type"] == "approved"][0]["by"] == "user:email_link"
    assert len(user_mail(email.outbox, "appointment_requested")) == 1
    # second click: friendly "already handled" page, nothing sent twice
    again = client.get(url, params={"do": "approve", "token": tok})
    assert again.status_code == 200 and "Already handled" in again.text
    assert client.post(url, params={"do": "approve", "token": tok}).status_code == 409
    assert len(clinic_mail(email.outbox)) == 1


def test_decline_link(client, db, planned, fakes):
    email, _ = fakes
    user, appt = planned
    tok = link(user_mail(email.outbox, "appointment_approval_needed")[0].text, "decline")
    page = client.get(f"/api/appointments/{appt.id}/action", params={"do": "decline", "token": tok})
    assert page.status_code == 200 and "Decline this request" in page.text
    res = client.post(f"/api/appointments/{appt.id}/action", params={"do": "decline", "token": tok})
    assert res.status_code == 200 and "Nothing was sent" in res.text
    db.refresh(appt)
    assert appt.status == "cancelled" and appt.approval["status"] == "declined" and clinic_mail(email.outbox) == []
    # the approve button of the same e-mail no longer works
    tok2 = link(user_mail(email.outbox, "appointment_approval_needed")[0].text, "approve")
    assert client.post(f"/api/appointments/{appt.id}/action", params={"do": "approve", "token": tok2}).status_code == 409


def test_action_links_are_bound_and_expire(client, db, planned, fakes):
    from datetime import datetime, timedelta, timezone

    user, appt = planned
    nonce = appt.approval["requested_at"]
    good = user_action_token(appt.id, "approve", nonce)
    assert not ba.verify_user_action_token(appt.id, "decline", nonce, good)        # action-bound
    assert not ba.verify_user_action_token(appt.id, "approve", "other-card", good)  # card-bound
    assert not ba.verify_user_action_token("other", "approve", nonce, good)        # appointment-bound
    old = user_action_token(appt.id, "approve", nonce, issued_at=datetime.now(timezone.utc) - timedelta(days=8))
    assert not ba.verify_user_action_token(appt.id, "approve", nonce, old)          # 7-day TTL
    res = client.get(f"/api/appointments/{appt.id}/action", params={"do": "approve", "token": old})
    assert res.status_code == 403 and "expired" in res.text
    assert client.post(f"/api/appointments/{appt.id}/action", params={"do": "approve", "token": "nope"}).status_code == 403
    assert client.get("/api/appointments/missing/action", params={"do": "approve", "token": good}).status_code == 404
    db.refresh(appt)
    assert appt.status == "awaiting_approval"


def test_followup_email_not_duplicated_when_approval_email_follows(db, fakes, monkeypatch):
    """One finding → one e-mail: the follow-up notification stays in-app when the approval card
    (with its own e-mail) is produced in the same run."""
    from app.services import followup
    from tests.test_risk_service import NOW, add_bp

    email, _ = fakes
    user = toronto_user(db)
    add_bp(db, user, NOW, 150, 95)
    had_hooks = list(risk_service.FOLLOWUP_HOOKS)
    followup.register_followup_hooks()
    try:
        rows = risk_service.assess_user(db, user, as_of=NOW, trigger="manual")
    finally:
        risk_service.FOLLOWUP_HOOKS[:] = had_hooks
    cardio = next(r for r in rows if r.pathway == "cardio")
    assert cardio.level == "followup_recommended"
    fu = notes(db, user, "followup_recommended")[0]
    assert fu.payload["email"] == {"sent": False, "reason": "covered_by_approval_email"}
    assert user_mail(email.outbox, "followup_recommended") == []
    assert len(user_mail(email.outbox, "appointment_approval_needed")) == 1

    # without the booking consent the follow-up e-mail is the only one and is sent
    solo = toronto_user(db, consents=("data_processing", "ai_assessment"))
    add_bp(db, solo, NOW, 150, 95)
    risk_service.assess_user(db, solo, as_of=NOW, trigger="manual")
    assert notes(db, solo, "followup_recommended")[0].payload["email"]["sent"] is True


# ---------------------------------------------------------------- B. confirmation + calendar approval

@pytest.fixture()
def confirmed(client, db, planned, fakes):
    email, cal = fakes
    user, appt = planned
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)
    ba.confirm_appointment(db, appt.id, 1, ba.confirm_token_for(appt, 1), calendar=cal)
    assert appt.status == "confirmed"
    return user, appt


def test_confirmation_email_attaches_ics_and_asks_before_writing_calendar(db, confirmed, fakes):
    email, cal = fakes
    user, appt = confirmed
    assert cal.events == {}  # nothing written without the user's OK
    assert appt.approval["calendar"]["status"] == "pending" and appt.approval["calendar"]["mode"] == "google"
    msg = user_mail(email.outbox, "appointment_confirmed")[0]
    assert msg.subject.startswith(f"Confirmed: {appt.clinic['name']}")
    assert "Add to Google Calendar" in msg.text and "never changes your calendar" in msg.text
    assert "Get directions" in msg.text and "health card" in msg.text.lower()
    assert len(msg.attachments) == 1
    ics = msg.attachments[0]
    assert ics.filename.endswith(".ics") and ics.content_type.startswith("text/calendar")
    text = ics.content.decode()
    assert "BEGIN:VCALENDAR" in text and f"UID:{appt.id}@caresync" in text and "TRIGGER:-PT1H" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    start = appt.proposed_slots[1]["start"]  # "2026-…T14:00:00-04:00" → UTC stamp
    from datetime import datetime

    assert f"DTSTART:{datetime.fromisoformat(start).astimezone(__import__('datetime').timezone.utc):%Y%m%dT%H%M%SZ}" in text
    n = notes(db, user, "appointment_confirmed")[0]
    assert n.payload["calendar"]["status"] == "pending" and "Add it to your calendar?" in n.body
    assert n.payload["calendar"]["add_url"].startswith(settings.base_url)


def test_calendar_link_page_then_post_writes_event(client, db, confirmed, fakes):
    email, cal = fakes
    user, appt = confirmed
    msg = user_mail(email.outbox, "appointment_confirmed")[0]
    m = re.search(rf"/api/appointments/{appt.id}/calendar\?token=([0-9]+\.[0-9a-f]+)", msg.text)
    tok = m.group(1)
    page = client.get(f"/api/appointments/{appt.id}/calendar", params={"token": tok})
    assert page.status_code == 200 and "Add to Google Calendar" in page.text and cal.events == {}
    # the .ics download works from the same link
    ics = client.get(f"/api/appointments/{appt.id}/calendar.ics", params={"token": tok})
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    assert ics.headers["content-disposition"].endswith('.ics"') and b"BEGIN:VEVENT" in ics.content
    done = client.post(f"/api/appointments/{appt.id}/calendar", params={"token": tok})
    assert done.status_code == 200 and "Added to your Google Calendar" in done.text
    db.refresh(appt)
    ev = cal.events[appt.calendar_event_id]
    assert ev["status"] == "confirmed" and ev["start"].isoformat() == appt.proposed_slots[1]["start"]
    assert appt.approval["calendar"]["status"] == "added" and appt.approval["calendar"]["by"] == "user:email_link"
    # second visit: "already in your calendar", no second event
    again = client.get(f"/api/appointments/{appt.id}/calendar", params={"token": tok})
    assert "Already in your calendar" in again.text and len(cal.events) == 1
    assert client.post(f"/api/appointments/{appt.id}/calendar", params={"token": "x"}).status_code == 403


def test_in_app_calendar_sync_add_and_skip(client, db, confirmed, fakes):
    _, cal = fakes
    user, appt = confirmed
    h = headers_for(user)
    sync = client.get(f"/api/appointments/{appt.id}/calendar-sync", headers=h).json()
    assert sync["status"] == "pending" and sync["mode"] == "google"
    assert sync["ics_url"].startswith(settings.base_url) and "calendar.google.com" in sync["google_url"]
    skipped = client.post(f"/api/appointments/{appt.id}/calendar-sync", json={"decision": "skip"}, headers=h).json()
    assert skipped["status"] == "skipped" and cal.events == {}
    added = client.post(f"/api/appointments/{appt.id}/calendar-sync", json={"decision": "add"}, headers=h).json()
    assert added["status"] == "added" and cal.events[added["event_id"]]["status"] == "confirmed"
    # another user cannot touch it
    other = toronto_user(db)
    assert client.get(f"/api/appointments/{appt.id}/calendar-sync", headers=headers_for(other)).status_code == 404
    # not confirmed → 409
    _, cal2 = FakeEmail(), FakeCalendar()
    pend = ba.run_booking_agent(db, user, make_assessment(db, user, pathway="general"), FakeLlm(), cal2, None, FakeEmail(), "en")
    assert client.post(f"/api/appointments/{pend.id}/calendar-sync", json={"decision": "add"}, headers=h).status_code == 409


def test_manual_mode_without_google_offers_ics_and_template_link(client, db, monkeypatch):
    """No Google Calendar connected: the e-mail/page never promise an automatic write; the user gets the
    .ics and a pre-filled Google Calendar link, and the API add returns 409."""
    fake_email, cal = FakeEmail(), FakeCalendar(connected=False)
    monkeypatch.setattr(email_mod, "get_email_provider", lambda: fake_email)
    monkeypatch.setattr(notify_mod, "get_email_provider", lambda: fake_email)
    monkeypatch.setattr(gcal, "get_calendar_client", lambda db, user_id: cal)
    user = toronto_user(db)
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, fake_email, "en")
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=fake_email)
    ba.confirm_appointment(db, appt.id, 0, ba.confirm_token_for(appt, 0), calendar=cal)
    assert appt.approval["calendar"]["mode"] == "manual"
    msg = user_mail(fake_email.outbox, "appointment_confirmed")[0]
    assert "calendar.google.com/calendar/render?action=TEMPLATE" in msg.html and "Download .ics" in msg.text
    assert "never changes your calendar" not in msg.text and len(msg.attachments) == 1
    tok = re.search(rf"/calendar\?token=([0-9]+\.[0-9a-f]+)", msg.html or "") or re.search(r"calendar\.ics\?token=([0-9]+\.[0-9a-f]+)", msg.text)
    tok = tok.group(1)
    page = client.get(f"/api/appointments/{appt.id}/calendar", params={"token": tok})
    assert page.status_code == 200 and "not connected" in page.text and "Open in Google Calendar" in page.text
    h = headers_for(user)
    assert client.get(f"/api/appointments/{appt.id}/calendar-sync", headers=h).json()["mode"] == "manual"
    assert client.post(f"/api/appointments/{appt.id}/calendar-sync", json={"decision": "add"}, headers=h).status_code == 409
    assert cal.events == {}


def test_legacy_automatic_calendar_mode(db, fakes, monkeypatch):
    """CALENDAR_WRITE_REQUIRES_APPROVAL=false keeps the old behaviour: tentative hold, auto-confirm."""
    monkeypatch.setattr(settings, "calendar_write_requires_approval", False)
    email, cal = fakes
    user = toronto_user(db)
    appt = ba.run_booking_agent(db, user, make_assessment(db, user), FakeLlm(), cal, None, email, "en")
    ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)
    assert cal.events[appt.calendar_event_id]["status"] == "tentative"
    ba.confirm_appointment(db, appt.id, 2, ba.confirm_token_for(appt, 2), calendar=cal)
    assert cal.events[appt.calendar_event_id]["status"] == "confirmed"
    assert appt.approval["calendar"]["status"] == "added" and appt.approval["calendar"]["by"] == "auto"
    msg = user_mail(email.outbox, "appointment_confirmed")[0]
    assert "is in your Google Calendar" in msg.text and len(msg.attachments) == 1


def test_profile_edit_updates_what_the_pending_card_says_the_clinic_receives(client, db, planned):
    user, appt = planned
    before = dict(appt.approval["shared_fields"])
    res = client.put("/api/profile", json={"first_name": "Alexandra", "phone": "+1 647-555-0111",
                                           "postal_code": "m4w1a8", "street": None},
                     headers=headers_for(user))
    assert res.status_code == 200, res.text
    db.expire_all()
    shared = db.get(type(appt), appt.id).approval["shared_fields"]
    assert shared["first_name"] == "Alexandra" and shared["name"].startswith("Alexandra")
    assert shared["phone"] == "+1 647-555-0111" and shared["postal_code"] == "M4W 1A8"
    assert "street" not in shared  # cleared in the profile → no longer sent
    for k in ("reason_category", "preferred_slots", "report_url"):  # per-request parts are kept
        assert shared.get(k) == before.get(k)
