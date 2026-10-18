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
