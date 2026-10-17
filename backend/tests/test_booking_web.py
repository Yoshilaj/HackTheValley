"""Website booking channel (booking-v2 §1.6 "web").

Unit tests (no browser): field mapping, abort rules, slot selection, confirmation reading.
Integration tests: real headless Chromium against the mock clinic on a live uvicorn (random port);
skipped automatically when Playwright's Chromium cannot be launched.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.api.routes import mock_clinic
from app.core.config import settings
from app.db.models import AppointmentMessage
from app.integrations import Clinic, Slot
from app.integrations.google_calendar import FakeCalendar
from app.integrations.google_places import FakePlaces
from app.providers.email import FakeEmail
from app.providers.llm import FakeLlm
from app.services import booking_agent as ba
from app.services.booking.channels import web as webmod
from app.services.booking.channels.base import BookingContext, PatientInfo
from app.services.booking.channels.detect import DetectionResult
from app.services.booking.channels.web import (
    WebChannel, WebField, build_fill_plan, choose_reason_option, choose_slot, fields_from_dicts, keyword_target,
    map_fields, read_confirmation, start_url_for,
)
from app.services.report_service import generate_report
from tests.booking_helpers import RAW_HEALTH, clinic_mail, make_assessment, toronto_user

TZ = "America/Toronto"
TOR = ZoneInfo(TZ)


def patient(**kw) -> PatientInfo:
    base = dict(name="Alex Tremblay", date_of_birth="1958-01-15", phone="+1 416-555-0199", email="alex@example.com",
                postal_code="M5S 1X5", address="401 Bloor St W", reason_category="Heart and blood-pressure check-up",
                preferred_slots=["Mon Oct 19, 2026, 10:00 AM–10:30 AM EDT"], report_url="https://app.example/r/tok")
    base.update(kw)
    return PatientInfo(**base)


def slot(y, mo, d, h, mi=0) -> Slot:
    s = datetime(y, mo, d, h, mi, tzinfo=TOR)
    return Slot(s, s + timedelta(minutes=30))


def F(i, label, type_="text", name="", required=False, tag=None, options=None) -> WebField:
    return WebField(index=i, tag=tag or ("select" if type_ == "select" else "textarea" if type_ == "textarea" else "input"),
                    type=type_, name=name or label.lower().replace(" ", "_"), label=label, required=required,
                    options=options or [])


SLOT_OPTIONS = [{"value": "", "text": "Choose…", "group": ""}] + [
    {"value": datetime(2026, 10, d, h, m, tzinfo=TOR).isoformat(), "text": f"{h}:{m:02d}", "group": f"Oct {d}"}
    for d in (19, 20) for h in (9, 10, 11, 14, 15) for m in (0, 30) if not (d == 19 and h == 10 and m == 0)]
REASON_OPTIONS = [{"value": ""}] + [{"value": r, "text": r} for r in mock_clinic.REASONS]


def mock_fields() -> list[WebField]:
    return [F(0, "First name *", name="first_name", required=True), F(1, "Last name *", name="last_name", required=True),
            F(2, "Date of birth *", "date", "date_of_birth", True), F(3, "Phone *", "tel", "phone", True),
            F(4, "E-mail *", "email", "email", True), F(5, "Postal code *", name="postal_code", required=True),
            F(6, "Reason for visit *", "select", "reason", True, options=REASON_OPTIONS),
            F(7, "Preferred date and time *", "select", "appointment_time", True, options=SLOT_OPTIONS),
            F(8, "Anything else we should know? (optional)", "textarea", "notes"),
            F(9, "I agree that the clinic may contact me by phone or e-mail and have read the privacy notice.",
              "checkbox", "consent", True)]


# ================================================================ mapping

def test_keyword_mapping_of_mock_clinic_form():
    got = {f.name: keyword_target(f) for f in mock_fields()}
    assert got == {"first_name": "first_name", "last_name": "last_name", "date_of_birth": "date_of_birth",
                   "phone": "phone", "email": "email", "postal_code": "postal_code", "reason": "reason_category",
                   "appointment_time": "preferred_slots", "notes": "report_url", "consent": "consent"}


@pytest.mark.parametrize("label,type_,expected", [
    ("Full name", "text", "name"), ("Your name", "text", "name"), ("Password", "password", "password"),
    ("Ontario health card number", "text", "government_id"), ("OHIP #", "text", "government_id"),
    ("Card number", "text", "payment"), ("Family doctor's name", "text", "family_doctor_name"),
    ("Family doctor phone", "tel", "family_doctor_phone"), ("Family physician clinic", "text", "family_doctor_clinic"),
    ("Family doctor fax", "tel", "unknown"), ("Insurance policy number", "text", "unknown"),
    ("Gender", "text", "sex"), ("Sex", "radio", "sex"), ("Subscribe to our newsletter and promotions", "checkbox", "unknown"),
    ("I agree to a $50 cancellation fee", "checkbox", "unknown"), ("Preferred date", "date", "preferred_slots"),
    ("Mobile", "tel", "phone"), ("Message", "textarea", "report_url"), ("Street address", "text", "street"),
    ("Home address", "text", "address"), ("City", "text", "city"), ("Province", "select", "province"),
    ("Preferred language", "text", "preferred_language"), ("Do you have a referral?", "select", "has_referral"),
    ("Emergency contact phone", "tel", "unknown"), ("Referring physician", "text", "unknown"),
    ("Medications", "text", "unknown"), ("Medical history", "textarea", "unknown"),
])
def test_keyword_rules(label, type_, expected):
    assert keyword_target(F(0, label, type_)) == expected


def test_fake_llm_uses_rules_and_scripted_llm_is_used_but_cannot_unblock():
    fields = [F(0, "Patient", name="p1", required=True), F(1, "HC", name="hc_num"), F(2, "Ref", name="x")]
    fields[1].label = "Health card"
    assert map_fields(fields, FakeLlm()) == {0: "unknown", 1: "government_id", 2: "unknown"}
    llm = FakeLlm(extract_queue=[{"fields": [{"index": 0, "maps_to": "name"}, {"index": 1, "maps_to": "phone"},
                                             {"index": 2, "maps_to": "bogus"}]}])
    assert map_fields(fields, llm) == {0: "name", 1: "government_id", 2: "unknown"}
    assert llm.calls[0]["purpose"] == "booking_web_fields"


def test_llm_never_sees_patient_values():
    seen = {}

    class Spy:
        def extract(self, system, content, schema, **kw):
            seen["content"] = content
            assert schema["properties"]["fields"]["items"]["properties"]["maps_to"]["enum"][-1] == "payment"
            return {"fields": []}

    map_fields(mock_fields(), Spy())
    assert "Alex" not in seen["content"] and "1958" not in seen["content"] and "First name" in seen["content"]


def test_radio_groups_and_hidden_fields():
    raw = [{"tag": "input", "type": "hidden", "name": "csrf"},
           {"tag": "input", "type": "radio", "name": "new", "value": "yes", "label": "Yes", "group_label": "New patient?"},
           {"tag": "input", "type": "radio", "name": "new", "value": "no", "label": "No", "group_label": "New patient?"},
           {"tag": "input", "type": "text", "name": "trap", "visible": False},
           {"tag": "input", "type": "text", "name": "full_name", "label": "Full name", "required": True}]
    fs = fields_from_dicts(raw)
    assert [(f.name, f.type) for f in fs] == [("new", "radio"), ("full_name", "text")]
    assert [o["value"] for o in fs[0].options] == ["yes", "no"]


# ================================================================ fill plan / abort rules

def test_fill_plan_types_only_allow_listed_values():
    fields = mock_fields()
    approved = [slot(2026, 10, 19, 10), slot(2026, 10, 20, 14)]
    p = patient()
    plan = build_fill_plan(fields, map_fields(fields, FakeLlm()), p, approved, TZ)
    assert plan.abort is None
    values = {a.field.name: a.value for a in plan.actions}
    assert values["first_name"] == "Alex" and values["last_name"] == "Tremblay"
    assert values["reason"] == "Heart and blood-pressure check-up" and values["consent"] == "check"
    assert values["notes"] == "Referral report (secure link): https://app.example/r/tok"
    # 10:00 on the 19th is taken -> exact match on the second approved slot wins
    assert values["appointment_time"] == approved[1].start.isoformat() and plan.slot.start == approved[1].start
    shared = set(map(str, p.shared_fields().values()))
    for name, v in values.items():
        if name not in ("first_name", "last_name", "reason", "appointment_time", "notes", "consent"):
            assert v in shared, name
    assert "401 Bloor" not in plan.summary()  # address withheld when a postal code exists


@pytest.mark.parametrize("extra,reason", [
    (F(10, "Password", "password", "pw"), "login required"),
    (F(10, "Ontario health card number *", name="ohip", required=True), "health-card number required"),
    (F(10, "Credit card number", name="cc"), "payment required"),
    (F(10, "Family doctor's name *", name="gp", required=True), "required field has no shared value: Family doctor's name *"),
    (F(10, "Current medications *", "textarea", "meds", True), "required field outside the allow-list: Current medications *"),
])
def test_abort_rules(extra, reason):
    fields = mock_fields() + [extra]
    plan = build_fill_plan(fields, map_fields(fields, FakeLlm()), patient(), [slot(2026, 10, 20, 14)], TZ)
    assert plan.abort == reason and plan.actions == []


def test_identity_fields_from_onboarding_filled():
    sex_opts = [{"value": "", "text": "Select…", "group": ""}, {"value": "M", "text": "Male", "group": ""},
                {"value": "F", "text": "Female", "group": ""}, {"value": "X", "text": "Other / non-binary", "group": ""}]
    prov_opts = [{"value": "", "text": "—", "group": ""}, {"value": "QC", "text": "Quebec", "group": ""},
                 {"value": "ON", "text": "Ontario", "group": ""}]
    yn = [{"value": "y", "text": "Yes", "group": ""}, {"value": "n", "text": "No", "group": ""}]
    extra = [F(10, "Sex *", "select", "sex", True, options=sex_opts),
             F(11, "Street address *", name="street", required=True), F(12, "City *", name="city", required=True),
             F(13, "Province *", "select", "prov", True, options=prov_opts),
             F(14, "Family doctor name", name="fd_name"), F(15, "Family doctor clinic", name="fd_clinic"),
             F(16, "Family doctor phone", "tel", "fd_phone"),
             F(17, "Do you have a referral?", "select", "ref", options=yn),
             F(18, "Apartment / unit (optional)", name="unit"), F(19, "Home address", name="home_addr")]
    fields = mock_fields() + extra
    p = patient(first_name="Alexandra", last_name="Tremblay-Roy", sex="female", street="401 Bloor St W",
                city="Toronto", province="ON", family_doctor_name="Dr. Anita Shah",
                family_doctor_clinic="Kensington FHT", family_doctor_phone="+1 416-555-0106", has_referral=False)
    plan = build_fill_plan(fields, map_fields(fields, None), p, [slot(2026, 10, 20, 14)], TZ)
    assert plan.abort is None
    v = {a.field.name: a.value for a in plan.actions}
    assert v["first_name"] == "Alexandra" and v["last_name"] == "Tremblay-Roy"   # legal parts, not a split
    assert v["sex"] == "F" and v["prov"] == "ON" and v["street"] == "401 Bloor St W" and v["city"] == "Toronto"
    assert v["fd_name"] == "Dr. Anita Shah" and v["fd_clinic"] == "Kensington FHT" and v["fd_phone"] == "+1 416-555-0106"
    assert v["ref"] == "n"
    assert "home_addr" not in v  # optional address withheld: the postal code is enough


def test_optional_blocked_or_unknown_fields_left_empty():
    fields = mock_fields() + [F(10, "Health card number (optional)", name="hc"), F(11, "Gender", name="gender")]
    plan = build_fill_plan(fields, map_fields(fields, None), patient(), [slot(2026, 10, 20, 14)], TZ)
    assert plan.abort is None and {"hc", "gender"}.isdisjoint(a.field.name for a in plan.actions)
    assert "Health card number (optional)" in plan.left_empty


def test_required_value_missing_or_no_close_slot_aborts():
    fields = mock_fields()
    plan = build_fill_plan(fields, map_fields(fields, None), patient(date_of_birth=None), [slot(2026, 10, 20, 14)], TZ)
    assert plan.abort == "required field has no shared value: Date of birth *"
    plan = build_fill_plan(fields, map_fields(fields, None), patient(), [slot(2026, 10, 23, 10)], TZ)
    assert plan.abort == "no open slot near the approved times"


# ================================================================ slots / options / confirmation

def test_choose_slot_exact_closest_and_none():
    assert choose_slot(SLOT_OPTIONS, [slot(2026, 10, 20, 9, 30)], TZ)[0] == datetime(2026, 10, 20, 9, 30, tzinfo=TOR).isoformat()
    value, s = choose_slot(SLOT_OPTIONS, [slot(2026, 10, 19, 10)], TZ)  # taken -> nearest the same day
    assert s.start in (datetime(2026, 10, 19, 9, 30, tzinfo=TOR), datetime(2026, 10, 19, 10, 30, tzinfo=TOR))
    assert choose_slot(SLOT_OPTIONS, [slot(2026, 10, 19, 19)], TZ) is None             # > 2 h away
    assert choose_slot(SLOT_OPTIONS, [slot(2026, 10, 21, 10)], TZ) is None             # other day
    utc = Slot(datetime(2026, 10, 20, 18, 0, tzinfo=ZoneInfo("UTC")), datetime(2026, 10, 20, 18, 30, tzinfo=ZoneInfo("UTC")))
    assert choose_slot(SLOT_OPTIONS, [utc], TZ)[0] == datetime(2026, 10, 20, 14, tzinfo=TOR).isoformat()
    text_opts = [{"value": "a1", "text": "Tue Oct 20, 2026 2:00 PM", "group": ""},
                 {"value": "a2", "text": "Tue Oct 20, 2026 3:00 PM", "group": ""}]
    assert choose_slot(text_opts, [slot(2026, 10, 20, 15)], TZ)[0] == "a2"


def test_choose_reason_option():
    assert choose_reason_option(REASON_OPTIONS, "Heart and blood-pressure check-up") == "Heart and blood-pressure check-up"
    assert choose_reason_option(REASON_OPTIONS, "Neurology consultation (movement changes)") == "Neurology consultation"
    assert choose_reason_option(REASON_OPTIONS, "Memory consultation") == "Memory consultation"
    assert choose_reason_option([{"value": "x", "text": "Physio"}, {"value": "o", "text": "Other"}], "Memory consultation") == "o"


def test_read_confirmation_variants():
    html = '<p>Your appointment is confirmed for <time datetime="2026-10-20T14:00:00-04:00">Tue</time></p>'
    c = read_confirmation("Your appointment is booked\nReference number: QW-ABCDEF\nconfirmed", html, TZ)
    assert c["status"] == "confirmed" and c["reference"] == "QW-ABCDEF"
    assert c["start"] == datetime(2026, 10, 20, 14, tzinfo=TOR)
    p = read_confirmation("Thanks! Your request has been received. We will call you to confirm. Confirmation #: 88231",
                          "", TZ)
    assert p["status"] == "pending" and p["reference"] == "88231"
    e = read_confirmation("Book an appointment\nPhone is required.", "", TZ, http_status=422)
    assert e["status"] == "error" and "required" in e["message"]
    llm = FakeLlm(extract_queue=[{"status": "pending", "reference": "INVENTED-1", "appointment_start": None,
                                  "message": None}])
    out = read_confirmation("We received your request. Reference number: QW-ABCDE2", "", TZ, llm)
    assert out["status"] == "pending" and out["reference"] == "QW-ABCDE2"  # hallucinated reference ignored


def test_start_url_for_stays_under_target():
    base = "http://localhost:8000/mock-clinic/login"
    assert start_url_for(base, {"form_url": "http://localhost:8000/mock-clinic/login/book"}).endswith("/login/book")
    assert start_url_for(base, {"form_url": "https://real-clinic.ca/book"}) == base
    assert start_url_for(base, {"form_url": "http://localhost:8000/mock-clinic/book"}) == base
    assert start_url_for(base, None) == base


# ================================================================ channel contract (no browser)

def _ctx(clinic: Clinic, detection=None, slots=None, llm=None) -> BookingContext:
    return BookingContext(db=None, user=SimpleNamespace(id="u-web", email="alex@example.com"), appointment=None,
                          clinic=clinic, slots=slots or [], clinic_tz=TZ, user_tz=TZ, patient=patient(), report_url="",
                          llm=llm, detection=detection)


def test_available_and_configured(monkeypatch):
    c = Clinic(id="c", name="C", specialty="cardiology", website="https://real-clinic.ca")
    ch = WebChannel()
    assert ch.available(c, _ctx(c, {"web": "web_blocked", "reason": "captcha"})) == "captcha"
    assert ch.available(c, _ctx(c, {"web": "none", "reason": "no booking form found"})) == "no booking form found"
    monkeypatch.setattr(webmod, "chromium_installed", lambda: True)
    assert ch.available(c, _ctx(c, {"web": "web_form"})) is None
    assert ch.available(c, _ctx(c, None)) == "website not checked"
    monkeypatch.setattr(settings, "web_booking_enabled", False)
    assert not ch.configured() and ch.available(c, _ctx(c, {"web": "web_form"})) == "not configured"
    assert ch.execute(SimpleNamespace(id="a"), _ctx(c, {"web": "web_form"})).status == "skipped"


def test_hard_timeout_never_submits(monkeypatch):
    monkeypatch.setattr(webmod, "chromium_installed", lambda: True)
    c = Clinic(id="c", name="C", specialty="cardiology", website="https://real-clinic.ca")
    ch = WebChannel(hard_timeout_s=0.2)
    monkeypatch.setattr(ch, "_drive", lambda *a: time.sleep(1))
    res = ch.execute(SimpleNamespace(id="a"), _ctx(c, {"web": "web_form"}))
    assert res.status == "skipped" and "timed out" in res.reason
    assert res.target["value"].endswith("/mock-clinic") and res.target["redirected"]  # sandbox redirect


# ================================================================ integration: Chromium + live mock clinic

_LAUNCH: dict[str, bool] = {}


def chromium_launchable() -> bool:
    if "ok" not in _LAUNCH:
        def probe():
            try:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as pw:
                    pw.chromium.launch(headless=True, args=webmod.chromium_args()).close()
                _LAUNCH["ok"] = True
            except Exception:
                _LAUNCH["ok"] = False
        t = threading.Thread(target=probe, daemon=True)
        t.start()
        t.join(60)
        _LAUNCH.setdefault("ok", False)
    return _LAUNCH["ok"] and webmod.chromium_installed()


@pytest.fixture(scope="module")
def live_base(app):
    if not chromium_launchable():
        pytest.skip("Playwright Chromium is not available")
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    mp = pytest.MonkeyPatch()
    base = f"http://127.0.0.1:{port}"
    mp.setattr(settings, "base_url", base)
    mp.setattr(settings, "web_booking_enabled", True)
    yield base
    mp.undo()
    server.should_exit = True
    th.join(5)


@pytest.fixture()
def live(live_base, monkeypatch):
    monkeypatch.setattr(settings, "base_url", live_base)
    monkeypatch.setattr(settings, "web_booking_enabled", True)
    mock_clinic.store.reset()
    yield live_base
    mock_clinic.store.reset()


def _open_slots_near(n=3) -> list[Slot]:
    opts = mock_clinic.open_slots()
    picks = [opts[0], opts[len(opts) // 2], opts[-1]][:n]
    return [Slot.from_any(o["value"]) for o in picks]


def test_drives_real_mock_clinic(live):
    c = Clinic(id="c", name="Bloor (demo)", specialty="cardiology", website=f"{live}/mock-clinic")
    slots = _open_slots_near()
    res = WebChannel().execute(SimpleNamespace(id="appt-1", user_id="u-web"),
                               _ctx(c, {"web": "web_form", "form_url": f"{live}/mock-clinic/book"}, slots))
    assert res.status == "confirmed", res.reason
    booking = mock_clinic.store.all()[0]
    assert res.external_ref == booking["reference"] and res.slot["start"] == booking["slot_start"] == slots[0].start.isoformat()
    assert booking["first_name"] == "Alex" and booking["postal_code"] == "M5S 1X5"
    assert booking["notes"] == "Referral report (secure link): https://app.example/r/tok"
    assert not booking["health_card_provided"]
    shots = res.details["screenshots"]
    assert set(shots) == {"before_submit", "result"} and all(Path(p).read_bytes()[:4] == b"\x89PNG" for p in shots.values())
    assert res.messages[0].external_id == f"web:{booking['reference']}" and "Alex" in res.messages[0].body


def test_follows_book_online_link_from_home(live):
    c = Clinic(id="c", name="Bloor (demo)", specialty="cardiology", website=f"{live}/mock-clinic")
    res = WebChannel().execute(SimpleNamespace(id="appt-2", user_id="u-web"),
                               _ctx(c, {"web": "web_form"}, _open_slots_near(1)))
    assert res.status == "confirmed" and res.details["pages"][:2] == [f"{live}/mock-clinic", f"{live}/mock-clinic/book"]


@pytest.mark.parametrize("variant,reason", [("login", "login required"), ("captcha", "captcha"),
                                            ("health-card", "health-card number required"),
                                            ("email-only", "no booking form found")])
def test_walls_at_runtime_abort_without_submitting(live, variant, reason):
    c = Clinic(id="c", name="X (demo)", specialty="cardiology", website=f"{live}/mock-clinic/{variant}")
    res = WebChannel().execute(SimpleNamespace(id="appt-3", user_id="u-web"),
                               _ctx(c, {"web": "web_form", "form_url": f"{live}/mock-clinic/{variant}/book"},
                                    _open_slots_near(1)))
    assert (res.status, res.reason) == ("skipped", reason)
    assert mock_clinic.store.all() == [] and "before_submit" not in res.details["screenshots"]


@pytest.fixture()
def booking_setup(db, live):
    user = toronto_user(db)
    assessment = make_assessment(db, user)
    report = generate_report(db, user, assessment, FakeLlm(), "en")
    return user, assessment, report


def _plan(db, user, assessment, report, **kw):
    return ba.run_booking_agent(db, user, assessment, FakeLlm(), kw.pop("calendar"), kw.pop("places", None),
                                kw.pop("email"), "en", report=report, **kw)


def test_full_chain_approve_web_confirmed_with_calendar_event(db, booking_setup, live):
    user, assessment, report = booking_setup
    cal, email = FakeCalendar(), FakeEmail()
    appt = _plan(db, user, assessment, report, calendar=cal, email=email)  # Bloor seed -> /mock-clinic
    assert appt.status == "awaiting_approval"
    by = {p["channel"]: p for p in appt.approval["channel_plan"]}
    assert by["web"]["available"] and by["web"]["target"].startswith(f"{live}/mock-clinic")
    assert mock_clinic.store.all() == []  # nothing submitted before approval
    out = ba.approve_appointment(db, user, appt.id, calendar=cal, email=email)
    assert out.status == "confirmed" and out.channel == "web", out.channel_attempts
    booking = mock_clinic.store.all()[0]
    assert out.external_ref == booking["reference"] and out.chosen_slot["start"] == booking["slot_start"]
    assert [(a["channel"], a["status"]) for a in out.channel_attempts] == [("web", "confirmed")]
    event = cal.events[out.calendar_event_id]
    assert event["status"] == "confirmed" and event["start"].isoformat() == booking["slot_start"]
    assert clinic_mail(email.outbox) == []  # the website booking replaced the e-mail
    rows = db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appt.id)).all()
    assert [(r.direction, r.channel) for r in rows] == [("out", "web")]
    blob = str(booking) + rows[0].body
    for raw in RAW_HEALTH:
        assert raw not in blob, raw
    assert f"/r/{report.share_token}" in booking["notes"]
    # the approved slot was booked exactly, or the closest open time on the same day
    approved = [s["start"] for s in out.proposed_slots]
    start = datetime.fromisoformat(booking["slot_start"])
    assert any(abs(start - datetime.fromisoformat(a)) <= webmod.MAX_SLOT_DRIFT for a in approved)


def _clinic_at(live, path, cid="gp_web") -> Clinic:
    return Clinic(id=cid, name="Web Test Clinic (demo)", specialty="cardiology", lat=43.66, lng=-79.40,
                  email="desk@webtest.example.com", website=f"{live}{path}", source="google_places",
                  timezone=TZ, opening_hours={k: [["09:00", "17:00"]] for k in ("mon", "tue", "wed", "thu", "fri")})


def test_login_variant_falls_through_to_email(db, booking_setup, live):
    user, assessment, report = booking_setup
    email = FakeEmail()
    places = FakePlaces([_clinic_at(live, "/mock-clinic/login")])
    appt = _plan(db, user, assessment, report, calendar=FakeCalendar(), email=email, places=places)
    web = {p["channel"]: p for p in appt.approval["channel_plan"]}["web"]
    assert web["available"] is False and web["reason"] == "login required"
    out = ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=email)
    assert out.status == "requested" and out.channel == "email"
    assert [(a["channel"], a["status"], a["reason"]) for a in out.channel_attempts][:1] == [("web", "skipped", "login required")]
    assert len(clinic_mail(email.outbox)) == 1 and mock_clinic.store.all() == []


def test_captcha_seen_at_runtime_falls_through_to_next_channel(db, booking_setup, live):
    """Detection said web_form (stale), the browser meets the CAPTCHA -> skipped, e-mail is sent."""
    user, assessment, report = booking_setup
    email = FakeEmail()
    places = FakePlaces([_clinic_at(live, "/mock-clinic/captcha", "gp_captcha")])

    def stale_detector(url):
        return DetectionResult(website=url, web="web_form", form_url=f"{live}/mock-clinic/captcha/book")

    appt = _plan(db, user, assessment, report, calendar=FakeCalendar(), email=email, places=places,
                 detector=stale_detector)
    assert {p["channel"]: p for p in appt.approval["channel_plan"]}["web"]["available"]
    out = ba.approve_appointment(db, user, appt.id, calendar=FakeCalendar(), email=email)
    attempts = [(a["channel"], a["status"], a.get("reason")) for a in out.channel_attempts]
    assert attempts[0] == ("web", "skipped", "captcha") and attempts[1][:2] == ("email", "requested")
    assert out.status == "requested" and out.channel == "email" and mock_clinic.store.all() == []


# ================================================================ request guard / submit race (review round 2)

def test_request_allowed_rules(monkeypatch):
    monkeypatch.setattr(webmod, "_host_is_public", lambda host: host == "cdn.example.com")
    target = "http://127.0.0.1:8000/mock-clinic"
    ok = webmod.request_allowed
    assert ok("http://127.0.0.1:8000/mock-clinic/book", target, True, True)    # the (private) sandbox target
    assert ok("data:image/png;base64,xx", target, True, False)
    assert not ok("https://cdn.example.com/x.js", target, True, False)          # sandbox: no other host at all
    assert not ok("http://169.254.169.254/latest", target, False, False)       # live: private sub-resource
    assert ok("https://cdn.example.com/x.js", target, False, False)            # live: public sub-resource
    assert not ok("https://cdn.example.com/page", target, False, True)          # live: no off-site pages
    assert not ok("file:///etc/passwd", target, False, False)
    assert not ok("http://127.0.0.1:9999/", target, True, True)                 # other port = other origin


def test_begin_submit_refused_after_cancel_or_block():
    run = webmod._Run()
    run.cancelled.set()
    with pytest.raises(webmod.WebAbort):
        WebChannel._begin_submit(run)
    assert not run.submitted
    run2 = webmod._Run()
    run2.blocked.append("http://10.0.0.1/")
    with pytest.raises(webmod.WebAbort):
        WebChannel._begin_submit(run2)
    run3 = webmod._Run()
    WebChannel._begin_submit(run3)
    assert run3.submitted


def test_timed_out_run_never_submits_later(monkeypatch):
    monkeypatch.setattr(webmod, "chromium_installed", lambda: True)
    c = Clinic(id="c", name="C", specialty="cardiology", website="https://real-clinic.ca")
    ch = WebChannel(hard_timeout_s=0.1)
    events: list[str] = []

    def slow_drive(appt, ctx, target, start, run):
        time.sleep(0.4)  # still filling when the watchdog gives up
        try:
            ch._begin_submit(run)
            events.append("submitted")
        except webmod.WebAbort:
            events.append("refused")

    monkeypatch.setattr(ch, "_drive", slow_drive)
    res = ch.execute(SimpleNamespace(id="a"), _ctx(c, {"web": "web_form"}))
    assert res.status == "skipped" and "timed out" in res.reason
    time.sleep(0.6)
    assert events == ["refused"]


def test_redirect_off_the_target_is_blocked(live, app):
    """A page under the target that redirects to another host / a private address is never followed."""
    from fastapi.responses import RedirectResponse
    from fastapi.routing import APIRoute

    route = APIRoute("/mock-clinic/__redirect_probe", lambda: RedirectResponse("http://169.254.169.254/latest",
                                                                              status_code=302))
    app.router.routes.insert(0, route)
    try:
        c = Clinic(id="c", name="Bloor (demo)", specialty="cardiology", website=f"{live}/mock-clinic")
        res = WebChannel().execute(SimpleNamespace(id="appt-r", user_id="u-web"),
                                   _ctx(c, {"web": "web_form", "form_url": f"{live}/mock-clinic/__redirect_probe"},
                                        _open_slots_near(1)))
    finally:
        app.router.routes.remove(route)
    assert res.status == "skipped" and "blocked" in res.reason, res.reason
    assert mock_clinic.store.all() == []
