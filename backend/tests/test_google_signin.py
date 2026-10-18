"""Sign in with Google: POST /api/auth/google with a Google ID token (verification faked here)."""

import uuid

import pytest
from sqlalchemy import select

from app.api.routes import auth as auth_routes
from app.core.config import settings
from app.db.models import User

CLIENT_ID = "test-client.apps.googleusercontent.com"


@pytest.fixture()
def google(monkeypatch):
    """Google configured; each test sets the claims the (faked) verification returns."""
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    state = {"claims": None}

    def fake_verify(credential):
        if credential.startswith("bad"):
            raise ValueError("Token used too late")
        return state["claims"]

    monkeypatch.setattr(auth_routes, "verify_google_credential", fake_verify)
    return state


def _claims(email, verified=True):
    return {"email": email, "email_verified": verified, "given_name": "Ada", "family_name": "Lovelace",
            "name": "Ada Lovelace", "aud": CLIENT_ID}


TOKEN = "good-" + "x" * 40


def test_config_reports_the_public_client_id(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    assert client.get("/api/auth/google/config").json() == {"enabled": False, "client_id": None}
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    assert client.get("/api/auth/google/config").json() == {"enabled": True, "client_id": CLIENT_ID}


def test_first_sign_in_creates_the_account_then_reuses_it(client, db, google):
    email = f"ada-{uuid.uuid4().hex[:6]}@gmail.com"
    google["claims"] = _claims(email)
    first = client.post("/api/auth/google", json={"credential": TOKEN})
    assert first.status_code == 200, first.text
    user = db.scalar(select(User).where(User.email == email))
    assert user is not None and user.password_hash is None
    assert user.profile.first_name == "Ada" and user.profile.last_name == "Lovelace"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {first.json()['access_token']}"})
    assert me.json()["email"] == email

    again = client.post("/api/auth/google", json={"credential": TOKEN})
    assert again.json()["user_id"] == first.json()["user_id"]
    assert len(db.scalars(select(User).where(User.email == email)).all()) == 1


def test_signs_into_an_existing_password_account_with_the_same_email(client, db, google):
    email = f"pw-{uuid.uuid4().hex[:6]}@example.com"
    signup = client.post("/api/auth/signup", json={"email": email, "password": "password-123"}).json()
    google["claims"] = _claims(email.upper())  # e-mails match case-insensitively
    assert client.post("/api/auth/google", json={"credential": TOKEN}).json()["user_id"] == signup["user_id"]


def test_rejects_bad_tokens_and_unverified_emails(client, google):
    assert client.post("/api/auth/google", json={"credential": "bad-" + "x" * 40}).status_code == 401
    google["claims"] = _claims("someone@example.com", verified=False)
    assert client.post("/api/auth/google", json={"credential": TOKEN}).status_code == 401
    assert client.post("/api/auth/google", json={"credential": "short"}).status_code == 422


def test_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    assert client.post("/api/auth/google", json={"credential": TOKEN}).status_code == 503
