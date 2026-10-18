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


# ---------------------------------------------------------------- sessions (sign-out)

def test_token_response_names_the_account(client):
    email, body = _signup(client)
    assert body["email"] == email
    login = client.post("/api/auth/login", json={"email": email, "password": "password123"}).json()
    assert login["email"] == email


def test_logout_revokes_this_token_only(client):
    email, first = _signup(client)
    second = client.post("/api/auth/login", json={"email": email, "password": "password123"}).json()
    h1, h2 = _bearer(first["access_token"]), _bearer(second["access_token"])
    assert client.get("/api/auth/me", headers=h1).status_code == 200

    res = client.post("/api/auth/logout", headers=h1)
    assert res.status_code == 204
    assert res.headers["cache-control"] == "no-store"
    assert client.get("/api/auth/me", headers=h1).status_code == 401
    assert client.get("/api/auth/me", headers=h2).status_code == 200  # the other browser stays signed in
    # a revoked token cannot sign out again, or do anything else
    assert client.post("/api/auth/logout", headers=h1).status_code == 401


def test_logout_everywhere(client):
    email, first = _signup(client)
    second = client.post("/api/auth/login", json={"email": email, "password": "password123"}).json()
    assert client.post("/api/auth/logout", json={"all": True}, headers=_bearer(second["access_token"])).status_code == 204
    assert client.get("/api/auth/me", headers=_bearer(first["access_token"])).status_code == 401
    assert client.get("/api/auth/me", headers=_bearer(second["access_token"])).status_code == 401


def test_sessions_list_and_remote_revoke(client):
    email, first = _signup(client)
    second = client.post(
        "/api/auth/login", json={"email": email, "password": "password123"},
        headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                               "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"},
    ).json()
    h1 = _bearer(first["access_token"])
    rows = client.get("/api/auth/sessions", headers=h1).json()
    assert len(rows) == 2
    assert [r["current"] for r in rows].count(True) == 1
    phone = next(r for r in rows if not r["current"])
    assert phone["label"] == "Safari on iPhone" and phone["method"] == "password"

    assert client.delete(f"/api/auth/sessions/{phone['id']}", headers=h1).status_code == 204
    assert client.get("/api/auth/me", headers=_bearer(second["access_token"])).status_code == 401
    assert [r["id"] for r in client.get("/api/auth/sessions", headers=h1).json()] != [phone["id"]]
    assert client.delete(f"/api/auth/sessions/{phone['id']}", headers=h1).status_code == 404  # already gone


def test_sessions_are_private_to_their_user(client):
    _, a = _signup(client)
    _, b = _signup(client)
    mine = client.get("/api/auth/sessions", headers=_bearer(a["access_token"])).json()
    assert client.delete(f"/api/auth/sessions/{mine[0]['id']}", headers=_bearer(b["access_token"])).status_code == 404
    assert client.get("/api/auth/me", headers=_bearer(a["access_token"])).status_code == 200


def test_token_without_session_claim_still_works(client, demo_user):
    # tokens minted by tests / scripts carry no sid: valid until expiry, not revocable
    assert client.get("/api/auth/me", headers=_bearer(create_access_token(demo_user.id))).status_code == 200


def test_token_for_a_foreign_session_is_rejected(client):
    _, a = _signup(client)
    _, b = _signup(client)
    sid_b = client.get("/api/auth/sessions", headers=_bearer(b["access_token"])).json()[0]["id"]
    forged = create_access_token(a["user_id"], session_id=sid_b)
    assert client.get("/api/auth/me", headers=_bearer(forged)).status_code == 401


def test_device_tokens_list_and_revoke(client, auth_headers):
    created = client.post("/api/auth/device-token", json={"label": "Watch"}, headers=auth_headers).json()
    rows = client.get("/api/auth/device-tokens", headers=auth_headers).json()
    mine = next(r for r in rows if r["id"] == created["id"])
    assert mine["label"] == "Watch" and "token" not in mine and "token_hash" not in mine
    assert client.get("/api/auth/me", headers={"X-Device-Token": created["token"]}).status_code == 200
    assert client.delete(f"/api/auth/device-tokens/{created['id']}", headers=auth_headers).status_code == 204
    assert client.get("/api/auth/me", headers={"X-Device-Token": created["token"]}).status_code == 401
    assert client.delete(f"/api/auth/device-tokens/{created['id']}", headers=auth_headers).status_code == 404


def test_sign_in_attempts_are_rate_limited(client):
    from app.core.config import settings

    settings.rate_limit_enabled = True
    try:
        statuses = [
            client.post("/api/auth/login", json={"email": f"nobody-{i}@example.com", "password": "wrong-pass"}).status_code
            for i in range(25)
        ]
    finally:
        settings.rate_limit_enabled = False
    assert statuses[0] == 401 and statuses[-1] == 429
    assert statuses.count(401) == 20


def test_malformed_authorization_header(client):
    assert client.get("/api/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer "}).status_code == 401
