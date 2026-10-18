"""Medications (on Profile.medications), refill requests and dose tick-offs, per user."""

import uuid

from app.core.auth import create_access_token
from app.db.models import Profile, User
from app.services.context_builder import bp_treated_from


def _user(db, medications=None):
    u = User(email=f"meds-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.flush()
    db.add(Profile(user_id=u.id, medications=medications))
    db.commit()
    return u


def _h(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


METFORMIN = {"name": "Metformin", "dose": "500 mg", "frequency": "Twice daily with meals", "purpose": "Blood sugar",
             "prescriber": "Dr. Priya Nair", "refills": 1, "times": ["8:00 AM", "6:00 PM"], "days_left": 9,
             "take_with": "Food", "side_effects": ["Upset stomach"], "interactions": ["Alcohol"], "glyph": "M"}


def test_add_list_update_delete(client, db):
    u = _user(db)
    res = client.post("/api/medications", json=METFORMIN, headers=_h(u))
    assert res.status_code == 201, res.text
    med = res.json()
    assert med["id"] and med["times"] == ["8:00 AM", "6:00 PM"] and med["days_left"] == 9

    assert [m["name"] for m in client.get("/api/medications", headers=_h(u)).json()] == ["Metformin"]
    res = client.patch(f"/api/medications/{med['id']}", json={"refills": 3, "dose": "1000 mg"}, headers=_h(u))
    assert res.json()["refills"] == 3 and res.json()["dose"] == "1000 mg" and res.json()["purpose"] == "Blood sugar"
    assert client.delete(f"/api/medications/{med['id']}", headers=_h(u)).status_code == 204
    assert client.get("/api/medications", headers=_h(u)).json() == []


def test_existing_profile_entries_get_ids_and_keep_working_for_the_risk_engine(client, db):
    # Entries written by document extraction have no id and use "schedule".
    u = _user(db, medications=[{"name": "Amlodipine", "dose": "5 mg", "schedule": "once daily"}])
    meds = client.get("/api/medications", headers=_h(u)).json()
    assert meds[0]["id"] and meds[0]["frequency"] == "once daily"
    assert client.get("/api/medications", headers=_h(u)).json()[0]["id"] == meds[0]["id"]  # stable
    db.expire_all()
    stored = db.query(Profile).filter_by(user_id=u.id).one().medications
    assert bp_treated_from(stored, None) is True  # Framingham still sees the BP medication


def test_refills_request_cancel_and_no_duplicates(client, db):
    u = _user(db)
    mid = client.post("/api/medications", json=METFORMIN, headers=_h(u)).json()["id"]
    body = {"medication_ids": [mid], "pharmacy_id": "maple", "method": "pickup"}
    first = client.post("/api/refills", json=body, headers=_h(u)).json()
    again = client.post("/api/refills", json=body, headers=_h(u)).json()
    assert first[0]["id"] == again[0]["id"]  # an open request isn't duplicated
    assert [r["medication_id"] for r in client.get("/api/refills", headers=_h(u)).json()] == [mid]
    assert client.delete(f"/api/refills/{mid}", headers=_h(u)).status_code == 204
    assert client.get("/api/refills", headers=_h(u)).json() == []
    assert client.delete(f"/api/refills/{mid}", headers=_h(u)).status_code == 404  # nothing open now
    assert client.post("/api/refills", json={"medication_ids": ["nope"]}, headers=_h(u)).status_code == 404


def test_doses_are_saved_per_day(client, db):
    u = _user(db)
    res = client.put("/api/doses/2026-10-17", json={"taken": ["m1-8:00 AM", "m1-8:00 AM", "m2-8:00 PM"]}, headers=_h(u))
    assert res.json() == {"day": "2026-10-17", "taken": ["m1-8:00 AM", "m2-8:00 PM"]}
    assert client.get("/api/doses/2026-10-17", headers=_h(u)).json()["taken"] == ["m1-8:00 AM", "m2-8:00 PM"]
    assert client.get("/api/doses/2026-10-18", headers=_h(u)).json()["taken"] == []
    client.put("/api/doses/2026-10-17", json={"taken": []}, headers=_h(u))
    assert client.get("/api/doses/2026-10-17", headers=_h(u)).json()["taken"] == []


def test_users_cannot_see_or_change_each_others_medications(client, db):
    alice, bob = _user(db), _user(db)
    mid = client.post("/api/medications", json=METFORMIN, headers=_h(alice)).json()["id"]
    client.put("/api/doses/2026-10-17", json={"taken": [f"{mid}-8:00 AM"]}, headers=_h(alice))
    client.post("/api/refills", json={"medication_ids": [mid]}, headers=_h(alice))

    assert client.get("/api/medications", headers=_h(bob)).json() == []
    assert client.patch(f"/api/medications/{mid}", json={"refills": 0}, headers=_h(bob)).status_code == 404
    assert client.delete(f"/api/medications/{mid}", headers=_h(bob)).status_code == 404
    assert client.post("/api/refills", json={"medication_ids": [mid]}, headers=_h(bob)).status_code == 404
    assert client.delete(f"/api/refills/{mid}", headers=_h(bob)).status_code == 404
    assert client.get("/api/refills", headers=_h(bob)).json() == []
    assert client.get("/api/doses/2026-10-17", headers=_h(bob)).json()["taken"] == []
    assert len(client.get("/api/refills", headers=_h(alice)).json()) == 1


def test_deleting_a_medication_withdraws_its_refill(client, db):
    u = _user(db)
    mid = client.post("/api/medications", json=METFORMIN, headers=_h(u)).json()["id"]
    client.post("/api/refills", json={"medication_ids": [mid]}, headers=_h(u))
    client.delete(f"/api/medications/{mid}", headers=_h(u))
    assert client.get("/api/refills", headers=_h(u)).json() == []


def test_requires_sign_in(client):
    for path in ("/api/medications", "/api/refills", "/api/doses/2026-10-17"):
        assert client.get(path).status_code == 401
