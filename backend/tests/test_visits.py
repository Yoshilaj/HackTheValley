"""Clinic directory, recommendations and self-booked visits, per user."""

import uuid
from datetime import datetime, timezone

from app.core.auth import create_access_token
from app.db.models import Profile, User
from app.services import clinics, visit_service

DOWNTOWN = {"lat": 43.6534, "lng": -79.3841}


def _user(db, **profile):
    u = User(email=f"visits-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.flush()
    db.add(Profile(user_id=u.id, **profile))
    db.commit()
    return u


def _h(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_open_slots_fall_inside_opening_hours():
    clinic = next(c for c in clinics.load_seed_clinics() if c.id == "seed_bloor_heart")
    # Friday 17 Oct 2026, 12:00 Toronto: tomorrow and Sunday are closed, so Monday comes first.
    slots = visit_service.open_slots(clinic, now=datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc))
    assert slots[0] == "2026-10-19T09:00"  # Mon opens 08:30; first offered time is 30 min in
    assert len(slots) == visit_service.SLOTS_PER_CLINIC
    for s in slots:
        d = datetime.fromisoformat(s)
        assert d.weekday() < 5 and "08:30" <= d.strftime("%H:%M") <= "18:30"
    assert visit_service.slot_label("2026-10-19T09:00") == "Mon, Oct 19 · 9:00 AM"


def test_providers_nearest_first_with_slots(client, db):
    u = _user(db)
    res = client.get("/api/providers", params=DOWNTOWN, headers=_h(u))
    assert res.status_code == 200, res.text
    items = res.json()
    assert len(items) == len(clinics.load_seed_clinics())
    km = [p["distance_km"] for p in items]
    assert km == sorted(km) and items[0]["distance"].endswith(" km")
    assert all(p["slots"] and p["next_slot"] and p["glyph"] for p in items)
    cardio = client.get("/api/providers", params={**DOWNTOWN, "specialty": "cardiology"}, headers=_h(u)).json()
    assert cardio and all(p["specialty"] == "Cardiology" for p in cardio)


def test_providers_use_the_profile_location_when_none_is_sent(client, db):
    u = _user(db, **DOWNTOWN)
    assert client.get("/api/providers", headers=_h(u)).json()[0]["distance_km"] is not None


def test_recommendations_fall_back_to_a_check_up_without_findings(client, db):
    u = _user(db, **DOWNTOWN)
    recs = client.get("/api/recommendations", headers=_h(u)).json()
    assert len(recs) == 1 and recs[0]["specialty"] == "Family medicine" and recs[0]["reason"]


def test_book_list_cancel_and_privacy(client, db):
    alice, bob = _user(db), _user(db)
    body = {"provider_id": "seed_bloor_heart", "slot": "2026-10-19T09:00", "note": "BP follow-up"}
    res = client.post("/api/bookings", json=body, headers=_h(alice))
    assert res.status_code == 201, res.text
    booking = res.json()
    assert booking["provider"]["name"].startswith("Bloor Street Heart Clinic") and booking["slot_label"].startswith("Mon")
    again = client.post("/api/bookings", json=body, headers=_h(alice)).json()
    assert again["id"] == booking["id"]  # same provider and time isn't booked twice

    assert [b["id"] for b in client.get("/api/bookings", headers=_h(alice)).json()] == [booking["id"]]
    assert client.get("/api/bookings", headers=_h(bob)).json() == []
    assert client.delete(f"/api/bookings/{booking['id']}", headers=_h(bob)).status_code == 404
    assert client.delete(f"/api/bookings/{booking['id']}", headers=_h(alice)).status_code == 204
    assert client.get("/api/bookings", headers=_h(alice)).json() == []
    assert client.delete(f"/api/bookings/{booking['id']}", headers=_h(alice)).status_code == 404  # already cancelled


def test_booking_a_provider_outside_the_directory(client, db):
    u = _user(db)
    missing = client.post("/api/bookings", json={"provider_id": "elsewhere-1"}, headers=_h(u))
    assert missing.status_code == 404
    res = client.post("/api/bookings", json={"provider_id": "elsewhere-1", "provider": {"name": "Dr. Somebody",
                                                                                         "specialty": "Dermatology"}},
                      headers=_h(u))
    assert res.status_code == 201 and res.json()["provider"]["name"] == "Dr. Somebody" and res.json()["slot"] is None
    assert client.post("/api/bookings", json={"provider_id": "x", "slot": "next week"}, headers=_h(u)).status_code == 422


def test_requires_sign_in(client):
    for path in ("/api/providers", "/api/recommendations", "/api/bookings"):
        assert client.get(path).status_code == 401


def test_open_slots_handle_midnight_and_stay_in_office_hours():
    from app.integrations import Clinic

    every_day = {d: [["00:00", "24:00"]] for d in visit_service.WEEKDAYS}  # Places "always open"
    clinic = Clinic(id="h", name="General Hospital", specialty="hospital", opening_hours=every_day,
                    timezone="America/Toronto")
    slots = visit_service.open_slots(clinic, now=datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc))
    assert slots[0] == "2026-10-18T08:30"
    assert all("08:30" <= s[-5:] <= "19:30" for s in slots)


class _FakeDirectoryPlaces:
    """Places stand-in: a couple of clinics per specialty, one shared by two searches."""

    is_fake = False

    def __init__(self):
        from app.integrations import Clinic

        self.calls = []
        self.make = lambda pid, name, s, lat: Clinic(id=f"gp_{pid}", place_id=pid, name=name, specialty=s, lat=lat,
                                                     lng=-79.3841, source="google_places")

    def search_text(self, query, lat, lng, radius_m=15000, specialty=None):
        self.calls.append((specialty, round(lat, 4), round(lng, 4)))
        return [self.make(f"{specialty}-1", f"{specialty} near", specialty, 43.66),
                self.make("shared", "Downtown Health Centre", specialty, 43.70)]


def test_directory_uses_real_places_from_the_geocoded_home_address(client, db, monkeypatch):
    from app.integrations import google_places
    from app.services.booking import discovery

    fake = _FakeDirectoryPlaces()
    monkeypatch.setattr(google_places, "places_configured", lambda: True)
    monkeypatch.setattr(google_places, "PlacesClient", lambda **kw: fake)
    monkeypatch.setattr(discovery, "resolve_origin", lambda profile, geocoder=None: ((43.6534, -79.3841), "geocoded", None))
    clinics._places_cache.clear()
    u = _user(db, address="100 Queen St W, Toronto", postal_code="M5H 2N2")

    items = client.get("/api/providers", headers=_h(u)).json()
    assert {s for s, _, _ in fake.calls} == set(visit_service.DIRECTORY_SPECIALTIES)
    assert [p["name"] for p in items].count("Downtown Health Centre") == 1  # merged across searches
    assert all(not p["fictional"] and p["distance_from"] == "home" for p in items)
    assert [p["distance_km"] for p in items] == sorted(p["distance_km"] for p in items)
    db.expire_all()
    profile = db.get(Profile, db.query(Profile.id).filter(Profile.user_id == u.id).scalar())
    assert (profile.lat, profile.lng) == (43.6534, -79.3841)  # geocoded once, kept

    calls = len(fake.calls)
    device = client.get("/api/providers", params={"lat": 43.6534, "lng": -79.3841}, headers=_h(u)).json()
    assert len(fake.calls) == calls  # same ~100 m cell: served from the cache
    assert device[0]["distance_from"] == "device"


def test_directory_without_a_location_shows_no_made_up_clinics(client, db, monkeypatch):
    from app.integrations import google_places
    from app.services.booking import discovery

    monkeypatch.setattr(google_places, "places_configured", lambda: True)
    monkeypatch.setattr(google_places, "PlacesClient", lambda **kw: _FakeDirectoryPlaces())
    monkeypatch.setattr(discovery, "resolve_origin", lambda profile, geocoder=None: (None, None, "no location"))
    assert client.get("/api/providers", headers=_h(_user(db))).json() == []


def test_hours_summary_handles_midnight_and_always_open():
    always = {d: [["00:00", "24:00"]] for d in visit_service.WEEKDAYS}
    assert visit_service.hours_summary(always) == "Open 24 hours"
    late = {d: [["08:00", "24:00"]] for d in visit_service.WEEKDAYS[:5]}
    assert visit_service.hours_summary(late) == "Mon-Fri 8 AM-12 AM"


def test_directory_keeps_only_doctors_clinics_and_real_hospitals():
    from app.integrations import Clinic

    def place(types, primary=None):
        return Clinic(id="x", name="x", specialty="hospital", extra={"types": types, "primary_type": primary})

    assert clinics.is_medical(place(["hospital", "health"], "hospital"), "hospital")
    assert not clinics.is_medical(place(["lodging"], "lodging"), "hospital")  # "Suite near the hospital"
    assert not clinics.is_medical(place(["health"], "health"), "hospital")  # a building, not a hospital
    assert not clinics.is_medical(place(["bus_stop", "transit_station"], "bus_stop"), "walk_in")
    assert not clinics.is_medical(place(["spa", "skin_care_clinic", "health"], "spa"), "walk_in")
    assert clinics.is_medical(place(["pharmacy", "doctor", "medical_clinic", "health"], "medical_clinic"), "walk_in")
    assert clinics.is_medical(place([]), "family_medicine")  # no type data: kept
