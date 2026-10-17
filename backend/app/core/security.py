"""Password hashing, token hashing, Fernet field encryption and audit logging."""

import hashlib
import secrets
from functools import lru_cache
from typing import Any

import bcrypt
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# NOTE: passlib 1.7.4 is incompatible with bcrypt>=4.1/5.x (its self-test hashes a
# >72-byte secret which bcrypt 5 rejects), so we call the bcrypt library directly.


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("ascii"))
    except ValueError:
        return False


def new_device_token() -> str:
    return "dt_" + secrets.token_urlsafe(32)


def new_share_token() -> str:
    return secrets.token_urlsafe(24)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---- Field encryption (OAuth tokens etc.) ----

@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.field_encryption_key
    if not key:
        if not settings.dev_mode:
            raise RuntimeError("FIELD_ENCRYPTION_KEY must be set when DEV_MODE=false")
        key = Fernet.generate_key().decode()
        log.warning("FIELD_ENCRYPTION_KEY unset; using an ephemeral dev key (data won't decrypt after restart)")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_str(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_str(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


# ---- Audit log ----

def audit(
    db: Session,
    user_id: str | None,
    action: str,
    resource: str,
    detail: dict[str, Any] | None = None,
    actor: str = "user",
    commit: bool = False,
):
    """Append an AuditLog row. Caller's transaction commits it unless commit=True."""
    from app.db.models import AuditLog

    row = AuditLog(user_id=user_id, actor=actor, action=action, resource=resource, detail=detail or {})
    db.add(row)
    if commit:
        db.commit()
    return row


# ---- Consent ----

def has_consent(db: Session, user_id: str, purpose: str) -> bool:
    """True if the user has an active (unrevoked) consent for `purpose`.
    Check before assessments (ai_assessment), emailing clinics (share_with_clinic),
    calendar use (calendar_access) and contacting family (emergency_contact)."""
    from sqlalchemy import select

    from app.db.models import Consent

    return db.scalar(
        select(Consent.id).where(
            Consent.user_id == user_id, Consent.purpose == purpose, Consent.revoked_at.is_(None)
        ).limit(1)
    ) is not None
