import uuid

from app.core.auth import create_access_token


def _email() -> str:
    return f"u{uuid.uuid4().hex[:10]}@example.com"


def _signup(client, email=None, password="password123"):
    email = email or _email()
    res = client.post("/api/auth/signup", json={"email": email, "password": password, "display_name": "T"})
    assert res.status_code == 201, res.text
    return email, res.json()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_signup_login_me(client):
    email, body = _signup(client)
    assert body["token_type"] == "bearer"
    me = client.get("/api/auth/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == email

    login = client.post("/api/auth/login", json={"email": email.upper(), "password": "password123"})
    assert login.status_code == 200
    assert login.json()["user_id"] == body["user_id"]


def test_signup_duplicate_and_bad_login(client):
    email, _ = _signup(client)
    assert client.post("/api/auth/signup", json={"email": email, "password": "password123"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": email, "password": "wrong-pass"}).status_code == 401


def test_unauthenticated_and_bad_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers=_bearer("garbage")).status_code == 401
    assert client.get("/api/auth/me", headers=_bearer(create_access_token(str(uuid.uuid4())))).status_code == 401


def test_demo_login(client, demo_user):
    res = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "demo1234"})
    assert res.status_code == 200
    assert res.json()["user_id"] == demo_user.id


def test_device_token(client, auth_headers, demo_user):
    res = client.post("/api/auth/device-token", json={"label": "iPhone"}, headers=auth_headers)
    assert res.status_code == 201
    token = res.json()["token"]
    me = client.get("/api/auth/me", headers={"X-Device-Token": token})
    assert me.status_code == 200 and me.json()["id"] == demo_user.id
    assert client.get("/api/auth/me", headers={"X-Device-Token": "nope"}).status_code == 401


def test_dev_mode_user_id_header(client, demo_user):
    from app.core.config import settings

    assert client.get("/api/auth/me", headers={"X-User-Id": demo_user.id}).json()["id"] == demo_user.id
    settings.dev_mode = False
    try:
        assert client.get("/api/auth/me", headers={"X-User-Id": demo_user.id}).status_code == 401
    finally:
        settings.dev_mode = True


def test_profile_get_update(client, auth_headers):
    res = client.get("/api/profile", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["age"] == 68 and res.json()["language"] == "en"

    res = client.put("/api/profile", json={"weight_kg": 72.5, "known_af": True}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["weight_kg"] == 72.5 and body["known_af"] is True
    assert body["ldl_mg_dl"] == 150.8  # untouched (3.9 mmol/L)
    assert client.put("/api/profile", json={"sex": "robot"}, headers=auth_headers).status_code == 422


def test_consent_grant_revoke(client):
    _, body = _signup(client)
    h = _bearer(body["access_token"])
    assert client.get("/api/consent", headers=h).json() == []
    res = client.post("/api/consent", json={"purpose": "ai_assessment"}, headers=h)
    assert [c["active"] for c in res.json()] == [True]
    # granting twice is idempotent
    assert len(client.post("/api/consent", json={"purpose": "ai_assessment"}, headers=h).json()) == 1
    res = client.post("/api/consent", json={"purpose": "ai_assessment", "granted": False}, headers=h)
    assert res.json()[0]["active"] is False and res.json()[0]["revoked_at"]
    assert client.post("/api/consent", json={"purpose": "bogus"}, headers=h).status_code == 422


def test_has_consent_helper(db, demo_user):
    from app.core.security import has_consent

    assert has_consent(db, demo_user.id, "share_with_clinic")
    assert not has_consent(db, str(uuid.uuid4()), "share_with_clinic")


def test_export_and_delete_cascade(client, db):
    from datetime import datetime, timezone

    from sqlalchemy import func, select

    from app.db.models import USER_OWNED_MODELS, BpReading, ChatMessage, RiskAssessment, Report, User

    _, body = _signup(client)
    uid, h = body["user_id"], _bearer(body["access_token"])
    client.post("/api/consent", json={"purpose": "data_processing"}, headers=h)
    now = datetime.now(timezone.utc)
    db.add(BpReading(user_id=uid, systolic=140, diastolic=90, measured_at=now, session="morning"))
    db.add(ChatMessage(user_id=uid, role="user", content="hello"))
    ra = RiskAssessment(user_id=uid, pathway="cardio", level="watch", priority_score=25)
    db.add(ra)
    db.flush()
    db.add(Report(assessment_id=ra.id, user_id=uid, markdown="# r", share_token=uuid.uuid4().hex))
    db.commit()

    export = client.get("/api/me/export", headers=h).json()
    assert export["user"]["id"] == uid and "password_hash" not in export["user"]
    assert len(export["bp_readings"]) == 1 and export["bp_readings"][0]["systolic"] == 140
    assert len(export["reports"]) == 1 and len(export["consents"]) == 1

    res = client.delete("/api/me", headers=h)
    assert res.status_code == 200 and res.json()["deleted"]
    db.expire_all()
    assert db.get(User, uid) is None
    for model in USER_OWNED_MODELS:
        assert db.scalar(select(func.count()).select_from(model).where(model.user_id == uid)) == 0
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_require_admin(app):
    import pytest
    from fastapi import HTTPException

    from app.core.auth import require_admin

    require_admin("test-admin-secret")
    with pytest.raises(HTTPException):
        require_admin("wrong")
    with pytest.raises(HTTPException):
        require_admin(None)
