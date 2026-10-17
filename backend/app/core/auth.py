"""Authentication dependencies.

Accepted credentials (first match wins):
1. `Authorization: Bearer <JWT>`   (web app; HS256, JWT_SECRET, 7 days)
2. `X-Device-Token: <token>`       (iOS Shortcut; long-lived, sha256 stored)
3. `X-User-Id: <user id>`          (ONLY when settings.dev_mode — frontend work before auth UI)
Admin endpoints use `require_admin` (`X-Admin-Secret: <ADMIN_SECRET>`).
"""

import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sha256_hex
from app.db.models import DeviceToken, User
from app.db.session import get_db

ALGORITHM = "HS256"


def create_access_token(user_id: str, expires_days: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=expires_days or settings.jwt_expire_days)
    return jwt.encode({"sub": user_id, "iat": now, "exp": exp}, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload.get("sub")


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def user_from_device_token(db: Session, token: str) -> User | None:
    row = db.scalar(select(DeviceToken).where(DeviceToken.token_hash == sha256_hex(token)))
    if not row:
        return None
    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return db.get(User, row.user_id)


def get_optional_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_device_token: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> User | None:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _unauthorized("Malformed Authorization header")
        user_id = decode_access_token(token)
        user = db.get(User, user_id) if user_id else None
        if not user:
            raise _unauthorized("Invalid or expired token")
        return user
    if x_device_token:
        user = user_from_device_token(db, x_device_token)
        if not user:
            raise _unauthorized("Invalid device token")
        return user
    if x_user_id and settings.dev_mode:
        user = db.get(User, x_user_id)
        if not user:
            raise _unauthorized("Unknown X-User-Id")
        return user
    return None


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise _unauthorized()
    return user


def require_admin(x_admin_secret: str | None = Header(default=None)) -> None:
    if not x_admin_secret or not hmac.compare_digest(x_admin_secret, settings.admin_secret):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin secret required")


def require_admin_or_dev(x_admin_secret: str | None = Header(default=None)) -> None:
    """Dev mode (``DEV_MODE=true``), or a valid ``X-Admin-Secret``."""
    if settings.dev_mode:
        return
    require_admin(x_admin_secret)
