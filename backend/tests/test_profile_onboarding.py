"""Onboarding identity fields, validation, booking readiness and the startup column migration."""

import uuid
from datetime import date, timedelta

from sqlalchemy import create_engine, inspect, text

from app.db.session import add_missing_columns, init_db

FULL = {
    "display_name": "Bob", "first_name": " Robert ", "last_name": "MacLeod", "date_of_birth": "1958-01-15",
    "sex": "male", "phone": "+1-416-555-0142", "street": "100 Queen St W", "city": "Toronto",
    "province": "on", "postal_code": "m5h2n2", "language": "en", "has_referral": False,
    "family_doctor": {"name": "Dr. Anita Shah", "clinic": "Kensington Family Health Team", "phone": "+1 416-555-0106"},
}


def _new_user(client):
    email = f"onb-{uuid.uuid4().hex[:8]}@example.com"
    res = client.post("/api/auth/signup", json={"email": email, "password": "password123", "display_name": "Bob"})
    assert res.status_code == 201
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_onboarding_fields_saved_and_normalised(client):
    h = _new_user(client)
    res = client.put("/api/profile", json=FULL, headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["first_name"] == "Robert" and body["last_name"] == "MacLeod" and body["display_name"] == "Bob"
    assert body["postal_code"] == "M5H 2N2" and body["province"] == "ON" and body["sex"] == "male"
    assert body["family_doctor"]["name"] == "Dr. Anita Shah" and body["has_referral"] is False
    assert body["address"] == "100 Queen St W, Toronto"  # kept in sync with the parts (geocoding)
    ready = client.get("/api/profile/booking-readiness", headers=h).json()
    assert ready["ready"] is True and ready["missing"] == [] and ready["recommended"] == []
    assert {"date_of_birth", "email", "postal_code", "family_doctor"} <= set(ready["fields_shared_with_clinics"])
    assert "health_card_number" in ready["never_shared"]
    assert not any("health_card" in f or "ohip" in f for f in ready["fields_shared_with_clinics"])


def test_booking_readiness_lists_missing_fields(client):
    h = _new_user(client)
    ready = client.get("/api/profile/booking-readiness", headers=h).json()
    assert ready["ready"] is False
    assert ready["missing"] == ["first_name", "last_name", "date_of_birth", "sex", "phone", "street", "city",
                                "province", "postal_code"]   # e-mail comes from the account
    assert ready["recommended"] == ["family_doctor", "has_referral"]
    client.put("/api/profile", json={"sex": "prefer_not", "contact_email": "Bob@Example.com"}, headers=h)
    ready = client.get("/api/profile/booking-readiness", headers=h).json()
    assert "sex" not in ready["missing"]
    assert client.get("/api/profile", headers=h).json()["contact_email"] == "bob@example.com"


def test_onboarding_validation(client):
    h = _new_user(client)
    future = (date.today() + timedelta(days=1)).isoformat()
    for bad in ({"postal_code": "12345"}, {"postal_code": "D1A 1A1"}, {"province": "XX"}, {"phone": "call me"},
                {"phone": "555-0142"}, {"date_of_birth": future}, {"date_of_birth": "1850-01-01"},
                {"contact_email": "nope"}, {"family_doctor": {"phone": "12"}}, {"sex": "robot"}):
        assert client.put("/api/profile", json=bad, headers=h).status_code == 422, bad
    for ok in ({"phone": "(416) 555-0142"}, {"phone": "+44 20 7946 0000"}, {"postal_code": "k1a-0b1"},
               {"postal_code": None}, {"province": "qc"}):
        assert client.put("/api/profile", json=ok, headers=h).status_code == 200, ok


def test_demo_profile_is_booking_ready(demo_user):
    from app.services.booking.common import booking_readiness

    r = booking_readiness(demo_user)
    assert r["ready"] and r["recommended"] == []
    p = demo_user.profile
    assert (p.first_name, p.last_name, p.street, p.city) == ("Robert", "MacLeod", "100 Queen St W", "Toronto")
    assert p.full_address == "100 Queen St W, Toronto, ON M5H 2N2" and p.age == 68


# ---------------------------------------------------------------- startup migration

def test_add_missing_columns_on_existing_sqlite_db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:  # an "old" profiles table without the onboarding columns
        conn.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(320), "
                          "password_hash VARCHAR(255), created_at DATETIME)"))
        conn.execute(text("CREATE TABLE profiles (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) UNIQUE, "
                          "display_name VARCHAR(200), language VARCHAR(8), timezone VARCHAR(64), updated_at DATETIME)"))
        conn.execute(text("INSERT INTO users (id, email) VALUES ('u1', 'old@example.com')"))
        conn.execute(text("INSERT INTO profiles (id, user_id, display_name, language, timezone) "
                          "VALUES ('p1', 'u1', 'Old', 'en', 'America/Toronto')"))
    init_db(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("profiles")}
    assert {"first_name", "last_name", "street", "city", "contact_email", "family_doctor", "has_referral",
            "date_of_birth", "postal_code"} <= cols
    with eng.connect() as conn:
        row = conn.execute(text("SELECT display_name, first_name, family_doctor FROM profiles")).one()
    assert tuple(row) == ("Old", None, None)  # existing rows kept, new columns NULL
    assert add_missing_columns(eng) == []    # idempotent


def test_add_missing_columns_skips_non_nullable(tmp_path, caplog):
    eng = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
    added = add_missing_columns(eng)
    assert added == ["users.password_hash"]  # only the plain nullable column
    # NOT NULL created_at / NOT NULL UNIQUE email are left for a real migration (logged)
    assert "cannot auto-add users.email" in caplog.text and "cannot auto-add users.created_at" in caplog.text
    assert add_missing_columns(eng) == []    # tables that do not exist are left to create_all


# ---------------------------------------------------------------- e-mail stays minimal

def test_clinic_email_has_name_and_dob_but_no_extra_identity_fields():
    from app.integrations import Slot
    from app.services.booking.channels.base import PatientInfo
    from app.services.booking.common import render_booking_email

    p = PatientInfo(name="Robert MacLeod", first_name="Robert", last_name="MacLeod", date_of_birth="1958-01-15",
                    sex="male", phone="+1-416-555-0142", email="demo@example.com", postal_code="M5H 2N2",
                    address="100 Queen St W, Toronto, ON M5H 2N2", street="100 Queen St W", city="Toronto",
                    province="ON", family_doctor_name="Dr. Anita Shah", family_doctor_clinic="Kensington FHT",
                    family_doctor_phone="+1 416-555-0106", has_referral=False, preferred_language="English",
                    reason_category="General check-up", report_url="http://x/r/tok")
    slots = [Slot.from_any("2026-11-03T10:00:00-05:00")]
    subject, html, body = render_booking_email(appointment_id="a1", clinic_name="Demo", patient=p, note=None,
                                               slots=slots, tz="America/Toronto", reply_to=None)
    for text_ in (html, body):
        assert "Robert MacLeod" in text_ and "1958-01-15" in text_ and "M5H 2N2" in text_
        for absent in ("100 Queen", "Anita Shah", "Kensington", "male", "English", "555-0106"):
            assert absent not in text_, absent
