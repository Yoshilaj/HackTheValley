"""Authentication dependencies.

Accepted credentials (first match wins):
1. `Authorization: Bearer <JWT>`   (web app; HS256, JWT_SECRET, 7 days)
2. `X-Device-Token: <token>`       (iOS Shortcut; long-lived, sha256 stored)
3. `X-User-Id: <user id>`          (ONLY when settings.dev_mode — frontend work before auth UI)
Admin endpoints use `require_admin` (`X-Admin-Secret: <ADMIN_SECRET>`).

Sessions: every web sign-in (signup, login, Google) creates a ``UserSession`` row and the token's
``sid`` claim names it. Signing out revokes the row, so the token stops working before it expires
(``user_from_access_token``). A token minted without ``sid`` (tests, scripts) is accepted while it
is valid but cannot be revoked.
"""

import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sha256_hex
from app.db.models import DeviceToken, User, UserSession
from app.db.session import get_db

ALGORITHM = "HS256"
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)  # how often last_seen_at is written


def create_access_token(user_id: str, expires_days: int | None = None, session_id: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=expires_days or settings.jwt_expire_days)
    claims: dict = {"sub": user_id, "iat": now, "exp": exp}
    if session_id:
        claims["sid"] = session_id
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token_claims(token: str) -> dict | None:
    """The verified claims of one of our tokens, or None (bad signature, expired, malformed)."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
    except jwt.PyJWTError:
        return None
    return payload if isinstance(payload.get("sub"), str) else None


def decode_access_token(token: str) -> str | None:
    """The user id a valid token names (signature and expiry only; see ``user_from_access_token``)."""
    claims = decode_token_claims(token)
    return claims["sub"] if claims else None


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


# ---------------------------------------------------------------- sessions

def session_label(user_agent: str | None) -> str | None:
    """A short "browser on device" label for the sessions list, e.g. "Safari on iPhone"."""
    ua = (user_agent or "").strip()
    if not ua:
        return None
    low = ua.lower()
    if "ipad" in low:
        device = "iPad"
    elif "iphone" in low:
        device = "iPhone"
    elif "android" in low:
        device = "Android"
    elif "mac os x" in low or "macintosh" in low:
        device = "Mac"
    elif "windows" in low:
        device = "Windows"
    elif "linux" in low:
        device = "Linux"
    else:
        device = None
    if "edg/" in low:
        browser = "Edge"
    elif "chrome/" in low or "crios/" in low:
        browser = "Chrome"
    elif "firefox/" in low or "fxios/" in low:
        browser = "Firefox"
    elif "safari/" in low:
        browser = "Safari"
    else:
        browser = None
    if browser and device:
        return f"{browser} on {device}"
    return (browser or device or ua)[:100]


def start_session(db: Session, user: User, *, method: str, user_agent: str | None = None) -> tuple[UserSession, str]:
    """Record a sign-in and mint its access token. Flushes; the caller commits."""
    row = UserSession(user_id=user.id, method=method, label=session_label(user_agent),
                      last_seen_at=datetime.now(timezone.utc))
    db.add(row)
    db.flush()
    return row, create_access_token(user.id, session_id=row.id)


def session_is_active(row: UserSession | None, now: datetime | None = None) -> bool:
    """Not revoked and younger than the token lifetime (older tokens have expired anyway)."""
    if row is None or row.revoked_at is not None:
        return False
    now = now or datetime.now(timezone.utc)
    return row.created_at + timedelta(days=settings.jwt_expire_days) > now


def revoke_session(db: Session, row: UserSession) -> None:
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)


def revoke_all_sessions(db: Session, user_id: str) -> int:
    rows = db.scalars(select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))).all()
    for row in rows:
        revoke_session(db, row)
    return len(rows)


def user_from_access_token(db: Session, token: str) -> tuple[User, UserSession | None] | None:
    """The user (and session) a token signs in, or None when the token is invalid, expired, revoked,
    or names a deleted user."""
    claims = decode_token_claims(token)
    if not claims:
        return None
    user = db.get(User, claims["sub"])
    if user is None:
        return None
    sid = claims.get("sid")
    if sid is None:
        return user, None
    row = db.get(UserSession, sid)
    if row is None or row.user_id != user.id or not session_is_active(row):
        return None
    now = datetime.now(timezone.utc)
    if row.last_seen_at is None or now - row.last_seen_at > SESSION_TOUCH_INTERVAL:
        row.last_seen_at = now
        db.commit()
    return user, row


def user_from_device_token(db: Session, token: str) -> User | None:
    row = db.scalar(select(DeviceToken).where(DeviceToken.token_hash == sha256_hex(token)))
    if not row:
        return None
    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return db.get(User, row.user_id)


# ---------------------------------------------------------------- dependencies

def bearer_token(authorization: str | None = Header(default=None)) -> str | None:
    """The raw bearer token from the Authorization header (None when absent)."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("Malformed Authorization header")
    return token.strip()


def get_optional_user(
    db: Session = Depends(get_db),
    token: str | None = Depends(bearer_token),
    x_device_token: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> User | None:
    if token:
        found = user_from_access_token(db, token)
        if not found:
            raise _unauthorized("Invalid or expired token")
        return found[0]
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


def get_current_session(
    db: Session = Depends(get_db),
    token: str | None = Depends(bearer_token),
    user: User = Depends(get_current_user),
) -> UserSession | None:
    """The UserSession behind this request's bearer token; None for device tokens, the dev header
    or a token minted without ``sid``."""
    if not token:
        return None
    found = user_from_access_token(db, token)
    return found[1] if found else None


def require_admin(x_admin_secret: str | None = Header(default=None)) -> None:
    if not x_admin_secret or not hmac.compare_digest(x_admin_secret, settings.admin_secret):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin secret required")


def require_admin_or_dev(x_admin_secret: str | None = Header(default=None)) -> None:
    """Dev mode (``DEV_MODE=true``), or a valid ``X-Admin-Secret``."""
    if settings.dev_mode:
        return
    require_admin(x_admin_secret)
