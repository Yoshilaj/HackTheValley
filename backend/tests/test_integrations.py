"""Google OAuth / Calendar / Places integrations and clinic ranking. No network."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.core.config import settings
from app.db.models import OAuthCredential
from app.integrations import Clinic, Slot
from app.integrations import google_oauth as go
from app.integrations.google_calendar import (
    CalendarClient, FakeCalendar, compute_free_slots, default_slots, get_calendar_client,
)
from app.integrations.google_places import FakePlaces, PlacesClient, get_places_client
from app.services import clinics as cs
from tests.test_report import headers_for, make_user

TZ = ZoneInfo("Asia/Tokyo")


@pytest.fixture()
def no_keys(monkeypatch):
    for key in ("google_client_id", "google_client_secret", "google_maps_api_key"):
        monkeypatch.setattr(settings, key, "")


# ---------------------------------------------------------------- factories

def test_factories_return_fakes_without_keys(db, no_keys):
    user = make_user(db)
    assert isinstance(go.get_oauth_client(), go.FakeGoogleOAuth)
    assert isinstance(get_places_client(), FakePlaces)
    cal = get_calendar_client(db, user.id)
    assert isinstance(cal, FakeCalendar) and cal.connected is False


def test_factories_return_real_clients_with_keys(db, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    monkeypatch.setattr(settings, "google_maps_api_key", "maps-key")
    oauth = go.get_oauth_client()
    assert isinstance(oauth, go.GoogleOAuth)
    url = oauth.authorization_url("st")
    assert "calendar.events" in url and "access_type=offline" in url and "client_id=cid" in url
    assert isinstance(get_places_client(), PlacesClient)
    user = make_user(db)
    go.store_tokens(db, user.id, {"access_token": "a", "refresh_token": "r", "expires_in": 3600})
    db.commit()
    assert isinstance(get_calendar_client(db, user.id), CalendarClient)  # no network until used


# ---------------------------------------------------------------- oauth

def test_state_roundtrip_and_tamper():
    state = go.make_state("user-1", now=1_000_000)
    assert go.verify_state(state, now=1_000_100) == "user-1"
    with pytest.raises(go.OAuthError):
        go.verify_state(state.replace("user-1", "user-2"), now=1_000_100)
    with pytest.raises(go.OAuthError):
        go.verify_state(state, now=1_000_000 + go.STATE_TTL_S + 1)
    with pytest.raises(go.OAuthError):
        go.verify_state("garbage")


def test_oauth_routes_store_encrypted_tokens(client, db, no_keys):
    user = make_user(db)
    h = headers_for(user)
    start = client.get("/api/integrations/google/start", headers=h).json()
    assert start["fake"] is True and "state=" in start["auth_url"]
    status0 = client.get("/api/integrations/status", headers=h).json()
    assert status0["google"]["connected"] is False and status0["places"]["source"] == "seed"

    res = client.get("/api/integrations/google/callback", params={"code": "abc", "state": start["state"]},
                     follow_redirects=False)
    assert res.status_code == 303 and "status=connected" in res.headers["location"]
    db.expire_all()
    cred = db.query(OAuthCredential).filter_by(user_id=user.id).one()
    assert "fake-access-abc" not in cred.encrypted_tokens
    assert go.load_tokens(cred)["access_token"] == "fake-access-abc"
    status1 = client.get("/api/integrations/status", headers=h).json()
    assert status1["google"]["connected"] is True and status1["google"]["calendar_consent"] is True
    assert get_calendar_client(db, user.id).connected is True

    bad = client.get("/api/integrations/google/callback", params={"code": "abc", "state": "x.1.bad"})
    assert bad.status_code == 400
    assert client.get("/api/integrations/google/start").status_code == 401
    assert client.delete("/api/integrations/google", headers=h).status_code == 204
    assert client.get("/api/integrations/status", headers=h).json()["google"]["connected"] is False


def test_refresh_helper_keeps_refresh_token(db):
    user = make_user(db)
    oauth = go.FakeGoogleOAuth()
    go.store_tokens(db, user.id, oauth.exchange_code("c1"), fake=True)
    cred = go.get_credential(db, user.id)
    cred.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    tokens = go.fresh_tokens(db, user.id, oauth)
    assert oauth.refreshed == ["fake-refresh-c1"]
    assert tokens["access_token"] == "fake-access-refreshed-1" and tokens["refresh_token"] == "fake-refresh-c1"


# ---------------------------------------------------------------- calendar

def test_compute_free_slots_respects_busy_hours_and_weekends():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=TZ)  # Friday
    busy = [(datetime(2026, 10, 19, 9, 0, tzinfo=TZ), datetime(2026, 10, 19, 12, 0, tzinfo=TZ))]
    slots = compute_free_slots(busy, days=4, business_hours=(9, 17), slot_minutes=30, tz="Asia/Tokyo", now=now)
    assert {s.start.date().isoformat() for s in slots} == {"2026-10-19", "2026-10-20"}  # Sat/Sun skipped
    monday = [s for s in slots if s.start.day == 19]
    assert monday[0].start.hour == 12 and len(monday) == 10
    assert all(9 <= s.start.hour < 17 and s.end.hour <= 17 for s in slots)


def test_default_slots_next_three_weekdays():
    slots = default_slots("Asia/Tokyo", now=datetime(2026, 10, 16, 12, 0, tzinfo=TZ))
    assert [(s.start.day, s.start.hour) for s in slots] == [(19, 10), (19, 14), (20, 10), (20, 14), (21, 10), (21, 14)]


def test_fake_calendar_events():
    cal = FakeCalendar()
    start = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
    eid = cal.create_event("x", start, start + timedelta(minutes=30), "d", tentative=True)
    assert cal.events[eid]["status"] == "tentative"
    assert cal.update_event(eid, status="confirmed") and cal.events[eid]["status"] == "confirmed"
    assert start not in {s.start for s in cal.free_slots(now=start - timedelta(days=1), days=2)}
    assert cal.delete_event(eid) and not cal.update_event(eid, status="confirmed")


def test_slot_parsing():
    s = Slot.from_any("2026-10-19T10:00:00+09:00")
    assert s.end - s.start == timedelta(minutes=30)
    with pytest.raises(ValueError):
        Slot.from_any("2026-10-19T10:00:00")


# ---------------------------------------------------------------- places + clinics

def test_seed_clinics_are_fictional():
    clinics = cs.load_seed_clinics()
    assert len(clinics) == 8
    assert {c.specialty for c in clinics} == set(cs.SPECIALTIES)
    assert all((c.email is None or c.email.endswith("example.com")) and c.fictional for c in clinics)


def test_clinics_ranked_by_distance():
    queen_west = SimpleNamespace(lat=43.6476, lng=-79.4040)
    bloor = SimpleNamespace(lat=43.6672, lng=-79.4036)
    assert cs.find_clinics(queen_west, "cardiology", places=None)[0].id == "seed_queen_west_cardio"
    ranked = cs.find_clinics(bloor, "cardiology", radius_km=50)
    assert [c.id for c in ranked] == ["seed_bloor_heart", "seed_queen_west_cardio"]
    assert ranked[0].distance_km < ranked[1].distance_km
    unranked = cs.find_clinics(SimpleNamespace(lat=None, lng=None), "neurology")
    assert [c.id for c in unranked] == ["seed_danforth_neuro"]
    assert unranked[0].distance_km is None
    assert cs.haversine_km(43.65, -79.38, 43.65, -79.38) == 0
    assert cs.find_clinics(bloor, "internal_medicine")[0].specialty == "family_medicine"  # legacy name


def test_pathway_specialty_map():
    assert [cs.specialty_for_pathway(p) for p in ("cardio", "cognitive", "parkinsons", "general")] == [
        "cardiology", "memory_clinic", "neurology", "family_medicine"]


def test_places_used_when_given_and_seed_on_error():
    me = SimpleNamespace(lat=43.6672, lng=-79.4036)
    custom = FakePlaces([Clinic(id="gp_x", name="X", specialty="cardiology", lat=43.67, lng=-79.40)])
    assert [c.id for c in cs.find_clinics(me, "cardiology", places=custom)] == ["gp_x"]
    assert custom.calls[0]["query"] == "cardiologist"

    class Broken:
        def search_text(self, *a, **kw):
            raise RuntimeError("quota")

    assert cs.find_clinics(me, "cardiology", places=Broken())[0].id == "seed_bloor_heart"


def test_places_client_parses_response(monkeypatch):
    import httpx

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, body=json, headers=headers)
        return httpx.Response(200, json={"places": [{
            "id": "ChIJ123", "displayName": {"text": "Some Clinic"}, "formattedAddress": "Toronto, ON",
            "location": {"latitude": 43.66, "longitude": -79.40}, "nationalPhoneNumber": "(416) 555-0100",
            "regularOpeningHours": {"weekdayDescriptions": ["Mon: 9-17"]}}]},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    out = PlacesClient(api_key="k").nearby(43.66, -79.40, "cardiologist", 5000, specialty="cardiology")
    assert captured["headers"]["X-Goog-Api-Key"] == "k" and "places.id" in captured["headers"]["X-Goog-FieldMask"]
    assert captured["body"]["locationBias"]["circle"]["radius"] == 5000.0
    assert captured["body"]["regionCode"] == "CA"
    c = out[0]
    assert (c.id, c.name, c.specialty, c.email, c.source, c.hours) == (
        "gp_ChIJ123", "Some Clinic", "cardiology", None, "google_places", "Mon: 9-17")
