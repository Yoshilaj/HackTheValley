"""Shared fixtures. Env is set BEFORE app import so settings/engine pick it up."""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="htv-tests-"))
os.environ.update(
    DATABASE_URL=f"sqlite:///{_TMP / 'test.db'}",
    DEV_MODE="true",
    SCHEDULER_ENABLED="false",
    EMBEDDING_PROVIDER="fake",
    LLM_PROVIDER="fake",
    STT_PROVIDER="fake",
    STORAGE_DIR=str(_TMP / "uploads"),
    JWT_SECRET="test-jwt-secret-with-enough-length-0123456789",
    ADMIN_SECRET="test-admin-secret",
    RATE_LIMIT_ENABLED="false",
    TTS_PROVIDER="fake",
    BOOKING_TARGET_MODE="sandbox",
    WEB_BOOKING_HEADED="false",
    WEB_BOOKING_SLOW_MO_MS="0",
)
# Tests must never reach real services: blank every third-party key, whatever .env says.
_SECRET_KEYS = (
    "ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "OPENAI_API_KEY", "HF_TOKEN", "GEMINI_API_KEY",
    "ELEVENLABS_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_MAPS_API_KEY",
    "RESEND_API_KEY", "RESEND_FROM_EMAIL", "CLINIC_FALLBACK_EMAIL", "RESEND_WEBHOOK_SECRET",
    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "ELEVENLABS_AGENT_ID",
    "ELEVENLABS_AGENT_PHONE_NUMBER_ID", "ELEVENLABS_WEBHOOK_SECRET", "SANDBOX_CLINIC_EMAIL",
    "SANDBOX_CLINIC_PHONE", "SANDBOX_CLINIC_WEBSITE", "BOOKING_REPLY_DOMAIN", "FIELD_ENCRYPTION_KEY",
    "APPOINTMENT_TOKEN_SECRET",
)
os.environ.update({k: "" for k in _SECRET_KEYS})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import create_access_token  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

# Belt and braces in case a .env overrides the environment.
settings.dev_mode = True
settings.scheduler_enabled = False
settings.embedding_provider = "fake"
settings.llm_provider = "fake"
settings.stt_provider = "fake"
settings.admin_secret = "test-admin-secret"
settings.rate_limit_enabled = False
settings.tts_provider = "fake"
settings.booking_target_mode = "sandbox"
settings.web_booking_headed = False  # never open browser windows / slow down in tests
settings.web_booking_slow_mo_ms = 0
for _k in _SECRET_KEYS:
    setattr(settings, _k.lower(), "")
db_session.configure(os.environ["DATABASE_URL"])
db_session.init_db(db_session.engine)


@pytest.fixture(scope="session")
def app():
    return fastapi_app


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    session = db_session.SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def demo_user(db):
    """The seeded demo user (idempotent; shared across tests in the session DB)."""
    from scripts.seed import seed_demo_user

    return seed_demo_user(db)


@pytest.fixture()
def auth_headers(demo_user):
    return {"Authorization": f"Bearer {create_access_token(demo_user.id)}"}


@pytest.fixture()
def admin_headers():
    return {"X-Admin-Secret": settings.admin_secret}
