"""Clinic discovery: Places Text Search (mocked httpx), geocoding, fallbacks, ranking, sandbox."""

from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.integrations import Clinic
from app.integrations.google_places import FakeGeocoder, FakePlaces, GeocodingClient, PlacesClient, parse_opening_hours
from app.services.booking import discovery, sandbox
from app.services.clinics import load_seed_clinics

TORONTO = SimpleNamespace(lat=43.6629, lng=-79.3957, address=None, postal_code=None, province="ON")


def _place(pid, name, lat=43.66, lng=-79.40, rating=4.0, status="OPERATIONAL", hours=True, **kw):
    p = {"id": pid, "displayName": {"text": name}, "formattedAddress": f"{pid} St, Toronto, ON",
         "location": {"latitude": lat, "longitude": lng}, "rating": rating, "businessStatus": status,
         "nationalPhoneNumber": "(416) 555-0150", "internationalPhoneNumber": "+1 416-555-0150",
         "websiteUri": f"https://{pid}.example.com"} | kw
    if hours:
        p["regularOpeningHours"] = {"periods": [{"open": {"day": d, "hour": 9, "minute": 0},
                                                 "close": {"day": d, "hour": 17, "minute": 0}} for d in range(1, 6)],
                                    "weekdayDescriptions": ["Monday: 9:00 AM – 5:00 PM"]}
    return p


def test_seed_clinics_are_fictional_toronto():
    clinics = load_seed_clinics()
    assert len(clinics) == 8 and all(c.fictional and c.timezone == "America/Toronto" for c in clinics)
    assert all(c.email is None or c.email.endswith("example.com") for c in clinics)
    assert all("555-01" in c.phone for c in clinics)
    sites = {c.website for c in clinics if c.website}
    base = settings.base_url.rstrip("/")
    assert {f"{base}/mock-clinic", f"{base}/mock-clinic/email-only", f"{base}/mock-clinic/login",
            f"{base}/mock-clinic/captcha"} == sites
    assert any(c.website is None and c.email is None and c.phone for c in clinics)  # phone-only


def test_seed_discovery_ranks_specialty_then_distance():
    r = discovery.discover_clinics(TORONTO, "cardio", places=None)
    assert r.source == "seed" and len(r.candidates) == 5
    assert [c.id for c in r.candidates[:2]] == ["seed_bloor_heart", "seed_queen_west_cardio"]
    assert all(c.specialty_match for c in r.candidates[:2]) and not r.candidates[2].specialty_match
    assert r.candidates[2].specialty in ("family_medicine", "walk_in")
    assert discovery.REFERRAL_NOTE in r.notes
    cog = discovery.discover_clinics(TORONTO, "cognitive")
    assert {c.specialty for c in cog.candidates[:3]} == {"memory_clinic", "neurology", "geriatrics"}


def test_places_text_search_request_and_parsing(monkeypatch):
    captured = []

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.append({"url": url, "json": json, "headers": headers})
        q = json["textQuery"]
        if q == "cardiologist":
            places = [_place("far", "Far Heart", lat=43.80, lng=-79.20, rating=5.0),
                      _place("near", "Near Heart", rating=3.5),
                      _place("closed", "Closed Heart", status="CLOSED_PERMANENTLY"),
                      _place("temp", "Temp Heart", status="CLOSED_TEMPORARILY", rating=4.9)]
        elif q == "family doctor":
            places = [_place("fam", "Family Doc", hours=False), _place("near", "Near Heart")]  # dup dropped
        else:
            places = []
        return httpx.Response(200, json={"places": places}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    r = discovery.discover_clinics(TORONTO, "cardio", places=PlacesClient(api_key="k"))
    body = captured[0]["json"]
    assert body["regionCode"] == "CA" and body["languageCode"] == "en"
    assert body["locationBias"]["circle"]["radius"] == 15000.0
    mask = captured[0]["headers"]["X-Goog-FieldMask"]
    for f in ("regularOpeningHours", "websiteUri", "nationalPhoneNumber", "internationalPhoneNumber",
              "businessStatus", "rating", "location"):
        assert f"places.{f}" in mask
    assert [q["json"]["textQuery"] for q in captured] == ["cardiologist", "family doctor", "walk-in clinic",
                                                          "general hospital"]
    ids = [c.place_id for c in r.candidates]
    assert "closed" not in ids
    assert ids[:3] == ["near", "far", "temp"]  # operational first, then distance; temp closed last of tier 0
    assert ids[3] == "fam" and r.source == "google_places"
    near = r.candidates[0]
    assert near.opening_hours["mon"] == [["09:00", "17:00"]] and "sat" not in near.opening_hours
    assert near.website == "https://near.example.com" and near.email is None


def test_places_failure_falls_back_to_seed(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", boom)
    r = discovery.discover_clinics(TORONTO, "cardio", places=PlacesClient(api_key="k"))
    assert r.source == "seed" and r.candidates[0].id == "seed_bloor_heart"
    assert any("error" in q for q in r.queries)


def test_geocoding_when_profile_has_no_coordinates(monkeypatch):
    prof = SimpleNamespace(lat=None, lng=None, address="401 Bloor St W", postal_code="M5S 1X5", province="ON")
    geo = FakeGeocoder({"bloor": (43.6672, -79.4036)})
    r = discovery.discover_clinics(prof, "cardio", geocoder=geo)
    assert r.origin == (43.6672, -79.4036) and r.origin_source == "geocoded"
    assert geo.calls == ["401 Bloor St W, M5S 1X5, ON, Canada"]
    assert r.candidates[0].distance_km < 0.1

    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen.update(params)
        return httpx.Response(200, json={"status": "OK", "results": [{"geometry": {"location": {"lat": 43.7,
                                                                                            "lng": -79.4}}}]}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", fake_get)
    assert GeocodingClient(api_key="k").geocode("M5S 1X5") == (43.7, -79.4)
    assert seen["components"] == "country:CA"
    nowhere = discovery.discover_clinics(SimpleNamespace(lat=None, lng=None, address=None, postal_code=None,
                                                         province=None), "general")
    assert nowhere.origin is None and nowhere.candidates


def test_parse_opening_hours_overnight_and_always_open():
    assert parse_opening_hours({"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]})["wed"] == [["00:00", "24:00"]]
    h = parse_opening_hours({"periods": [{"open": {"day": 5, "hour": 18, "minute": 0},
                                          "close": {"day": 6, "hour": 2, "minute": 30}}]})
    assert h == {"fri": [["18:00", "24:00"]], "sat": [["00:00", "02:30"]]}
    assert parse_opening_hours(None) is None


def test_legacy_fake_places_still_supported():
    custom = FakePlaces([Clinic(id="gp_x", name="X", specialty="cardiology", lat=43.66, lng=-79.40)])
    r = discovery.discover_clinics(TORONTO, "cardio", places=custom)
    assert r.candidates[0].id == "gp_x" and r.source == "fake_places"


@pytest.fixture()
def sandbox_settings(monkeypatch):
    for k in ("sandbox_clinic_email", "sandbox_clinic_phone", "sandbox_clinic_website", "clinic_fallback_email"):
        monkeypatch.setattr(settings, k, "")
    monkeypatch.setattr(settings, "booking_target_mode", "sandbox")
    return monkeypatch


def test_sandbox_redirects_every_channel(sandbox_settings):
    real = Clinic(id="gp_1", name="Real", specialty="cardiology", email="desk@realclinic.ca", phone="(416) 555-0150",
                  website="https://realclinic.ca/book", extra={"international_phone": "+1 416-555-0150"})
    assert sandbox.resolve_target("email", real) is None  # real address, no sandbox inbox -> no e-mail
    assert sandbox.missing_reason("email", real) == "sandbox: SANDBOX_CLINIC_EMAIL not set"
    assert sandbox.resolve_target("phone", real) is None
    web = sandbox.resolve_target("web", real)
    assert web.value == settings.base_url.rstrip("/") + "/mock-clinic" and web.real == "https://realclinic.ca/book"
    assert web.redirected and web.note == "sandbox: redirected from https://realclinic.ca/book"

    sandbox_settings.setattr(settings, "sandbox_clinic_email", "team-inbox@example.org")
    sandbox_settings.setattr(settings, "sandbox_clinic_phone", "+14165550199")
    sandbox_settings.setattr(settings, "sandbox_clinic_website", "https://sandbox.example.org/clinic")
    t = sandbox.resolve_target("email", real)
    assert (t.value, t.real, t.redirected) == ("team-inbox@example.org", "desk@realclinic.ca", True)
    assert sandbox.resolve_target("phone", real).value == "+14165550199"
    assert sandbox.resolve_target("phone", real).real == "+1 416-555-0150"
    assert sandbox.resolve_target("web", real).value == "https://sandbox.example.org/clinic"


def test_sandbox_keeps_fictional_targets_and_live_mode(sandbox_settings):
    seed = next(c for c in load_seed_clinics() if c.id == "seed_midtown_memory")
    t = sandbox.resolve_target("email", seed)
    assert t.value == seed.email and not t.redirected  # reserved example.com domain: undeliverable, safe
    assert sandbox.resolve_target("web", seed).value.endswith("/mock-clinic/login")
    sandbox_settings.setattr(settings, "booking_target_mode", "live")
    real = Clinic(id="r", name="R", specialty="x", email="desk@realclinic.ca")
    assert sandbox.resolve_target("email", real).value == "desk@realclinic.ca"
    assert sandbox.resolve_target("phone", real) is None


def test_geocoding_falls_back_to_places_when_the_geocoding_api_is_not_enabled(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return httpx.Response(200, json={"status": "REQUEST_DENIED", "error_message": "This API is not activated"},
                              request=httpx.Request("GET", url))

    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(json=json, headers=headers)
        return httpx.Response(200, json={"places": [{"location": {"latitude": 43.6527, "longitude": -79.3837}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    assert GeocodingClient(api_key="k").geocode("100 Queen St W, Toronto, ON M5H 2N2") == (43.6527, -79.3837)
    assert sent["json"]["textQuery"].startswith("100 Queen St W") and sent["headers"]["X-Goog-FieldMask"] == "places.location"
