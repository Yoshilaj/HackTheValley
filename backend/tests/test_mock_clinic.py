"""Mock clinic website (/mock-clinic, booking-v2 §3): pages, submissions, and detection per variant."""

import re
from datetime import date

import pytest

from app.api.routes import mock_clinic
from app.core.config import settings
from app.services.booking.channels.detect import FetchResponse, detect_channels


@pytest.fixture(autouse=True)
def fresh_store():
    mock_clinic.store.reset()
    yield
    mock_clinic.store.reset()


def fetcher(client):
    def fetch(url, timeout):
        r = client.get(url.replace("http://testserver", ""))
        return FetchResponse(status=r.status_code, text=r.text, url=str(r.url),
                             content_type=r.headers.get("content-type", ""))
    return fetch


def valid_form(slot_value: str, **kw) -> dict:
    form = {"first_name": "Alex", "last_name": "Tremblay", "date_of_birth": "1958-01-15", "phone": "+1 416-555-0199",
            "email": "alex@example.com", "postal_code": "M5S 1X5", "reason": "General check-up",
            "appointment_time": slot_value, "consent": "yes", "notes": ""}
    form.update(kw)
    return form


@pytest.mark.parametrize("variant,web,reason", [
    ("", "web_form", None),
    ("/email-only", "none", "no booking form found"),
    ("/login", "web_blocked", "login required"),
    ("/captcha", "web_blocked", "captcha"),
    ("/health-card", "web_blocked", "health-card number required"),
])
def test_detection_classifies_each_variant(client, variant, web, reason):
    d = detect_channels(f"http://testserver/mock-clinic{variant}", fetch=fetcher(client))
    assert (d.web, d.reason) == (web, reason)
    assert d.emails == [mock_clinic.CLINIC["email"]]
    assert all(p["url"].startswith(f"http://testserver/mock-clinic{variant}") for p in d.pages)  # stays in its variant
    if web == "web_form":
        assert d.form_url == "http://testserver/mock-clinic/book"
        names = {f["name"] for f in d.form["fields"]}
        assert {"first_name", "last_name", "date_of_birth", "phone", "email", "postal_code", "reason",
                "appointment_time", "consent"} <= names


def test_home_page_is_a_clinic_site(client):
    r = client.get("/mock-clinic")
    assert r.status_code == 200 and "Queen West Family &amp; Memory Clinic" in r.text
    assert 'href="/mock-clinic/book"' in r.text and "Book online" in r.text and "416-555-0142" in r.text
    assert "Monday" in r.text and "Fictional demo clinic" in r.text
    assert r.headers["x-robots-tag"].startswith("noindex")
    assert client.get("/mock-clinic/email-only").text.count("mailto:") >= 1
    assert "Book online" not in client.get("/mock-clinic/email-only").text


def test_booking_form_lists_open_slots_next_14_days(client):
    html = client.get("/mock-clinic/book").text
    values = re.findall(r'<option value="(\d{4}-\d{2}-\d{2}T[^"]+)"', html)
    assert len(values) > 50
    days = {v[:10] for v in values}
    assert all((date.fromisoformat(d) - date.today()).days <= 15 for d in days)
    assert not any("T12:" in v for v in values if date.fromisoformat(v[:10]).weekday() < 5)  # lunch break
    assert not any(date.fromisoformat(v[:10]).weekday() == 6 for v in values)  # closed Sundays
    for reason in mock_clinic.REASONS:
        assert f'<option value="{reason}"' in html


def test_submit_confirms_with_reference_and_admin_list(client):
    slot = mock_clinic.open_slots()[3]["value"]
    r = client.post("/mock-clinic/book", data=valid_form(slot), follow_redirects=False)
    assert r.status_code == 303
    ref = r.headers["location"].rsplit("/", 1)[1]
    assert re.fullmatch(r"QW-[A-Z2-9]{6}", ref)
    page = client.get(r.headers["location"]).text
    assert "Your appointment is booked" in page and ref in page and f'datetime="{slot}"' in page
    # the slot is gone for the next patient
    assert slot not in {s["value"] for s in mock_clinic.open_slots()}
    again = client.post("/mock-clinic/book", data=valid_form(slot))
    assert again.status_code == 422 and "no longer available" in again.text
    data = client.get("/mock-clinic/admin/bookings").json()
    assert data["count"] == 1 and data["bookings"][0]["reference"] == ref
    assert data["bookings"][0]["slot_start"] == slot and data["bookings"][0]["postal_code"] == "M5S 1X5"
    assert mock_clinic.store.path.exists()


def test_validation_errors_rerender_form(client):
    slot = mock_clinic.open_slots()[0]["value"]
    r = client.post("/mock-clinic/book", data=valid_form(slot, consent="", postal_code="12345", email="nope"))
    assert r.status_code == 422
    assert "valid Canadian postal code" in r.text and "valid e-mail" in r.text and "contact you" in r.text
    assert 'value="Alex"' in r.text  # values kept
    assert client.get("/mock-clinic/admin/bookings").json()["count"] == 0


def test_admin_bookings_needs_secret_outside_dev(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", False)
    assert client.get("/mock-clinic/admin/bookings").status_code == 403
    ok = client.get("/mock-clinic/admin/bookings", headers={"X-Admin-Secret": settings.admin_secret})
    assert ok.status_code == 200


def test_variants_walls(client):
    login = client.get("/mock-clinic/login/book").text
    assert 'type="password"' in login and "sign in to book" in login
    assert client.post("/mock-clinic/login/book", data={"email": "a@b.c", "password": "x"}).status_code == 401
    assert 'type="password"' in client.get("/mock-clinic/login/register").text
    captcha = client.get("/mock-clinic/captcha/book").text
    assert 'src="/mock-clinic/widget/captcha"' in captcha and "data-sitekey" in captcha
    assert client.get("/mock-clinic/widget/captcha").status_code == 200
    slot = mock_clinic.open_slots()[0]["value"]
    r = client.post("/mock-clinic/captcha/book", data=valid_form(slot))
    assert r.status_code == 422 and "CAPTCHA verification failed" in r.text
    assert client.get("/mock-clinic/email-only/book").status_code == 200
    assert client.post("/mock-clinic/email-only/book", data=valid_form(slot)).status_code == 405


def test_health_card_variant_stores_only_a_flag(client):
    slot = mock_clinic.open_slots()[0]["value"]
    assert client.post("/mock-clinic/health-card/book", data=valid_form(slot)).status_code == 422
    r = client.post("/mock-clinic/health-card/book", data=valid_form(slot, health_card="1234-567-890-AB"))
    assert r.status_code == 200 and "Your appointment is booked" in r.text  # redirect followed
    item = client.get("/mock-clinic/admin/bookings").json()["bookings"][0]
    assert item["health_card_provided"] is True and item["variant"] == "health-card"
    assert "1234" not in str(item)
