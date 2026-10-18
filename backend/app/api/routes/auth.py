from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import (
    get_current_session,
    get_current_user,
    revoke_all_sessions,
    revoke_session,
    session_is_active,
    start_session,
)
from app.core.config import settings
from app.core.ratelimit import rate_limit
from app.core.security import audit, hash_password, new_device_token, sha256_hex, verify_password
from app.db.base import utcnow
from app.db.models import DeviceToken, Profile, User, UserSession
from app.db.session import get_db
from app.schemas.auth import (
    DeviceTokenOut,
    DeviceTokenRequest,
    DeviceTokenResponse,
    GoogleConfigOut,
    GoogleLoginRequest,
    LoginRequest,
    LogoutRequest,
    SessionOut,
    SignupRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Sign-in attempts are limited per client IP (nobody is signed in yet): slows password guessing
# and credential stuffing. 20/minute leaves room for a household behind one address.
AUTH_RATE_LIMIT = "20/minute"
_limited = [Depends(rate_limit("auth", AUTH_RATE_LIMIT))]

# A hash to check against when the e-mail is unknown, so a wrong e-mail takes as long as a wrong
# password (otherwise response time tells an attacker which e-mails have accounts).
_UNKNOWN_USER_HASH = hash_password("not-a-real-password")


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def create_user(db: Session, email: str, password: str | None, display_name: str | None = None) -> User:
    user = User(email=_normalise_email(email), password_hash=hash_password(password) if password else None)
    user.profile = Profile(display_name=display_name)
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == _normalise_email(email)))
    if user is None:
        verify_password(password, _UNKNOWN_USER_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def issue_device_token(db: Session, user: User, label: str | None) -> tuple[DeviceToken, str]:
    raw = new_device_token()
    row = DeviceToken(user_id=user.id, token_hash=sha256_hex(raw), label=label)
    db.add(row)
    db.flush()
    return row, raw


def _sign_in(db: Session, user: User, request: Request, *, method: str, action: str) -> TokenResponse:
    """Record the sign-in (session + audit), commit, and answer with its token."""
    _, token = start_session(db, user, method=method, user_agent=request.headers.get("user-agent"))
    audit(db, user.id, action, "user", {"method": method})
    db.commit()
    return TokenResponse(access_token=token, user_id=user.id, email=user.email)


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, dependencies=_limited)
def signup(req: SignupRequest, request: Request, db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.email == _normalise_email(req.email))):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = create_user(db, req.email, req.password, req.display_name)
    return _sign_in(db, user, request, method="password", action="signup")


@router.post("/login", response_model=TokenResponse, dependencies=_limited)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = authenticate(db, req.email, req.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return _sign_in(db, user, request, method="password", action="login")


# ---------------------------------------------------------------- Sign in with Google
# The web app shows Google Identity Services (button + One Tap) with the public client id from
# /google/config, gets an ID token, and posts it here. The token is verified against Google's keys
# and GOOGLE_CLIENT_ID; the account is the one with that (Google-verified) e-mail, created on first
# sign-in. Separate from /api/integrations/google/*, which connects Google Calendar to an account.

_google_transport = None  # one HTTP session for fetching Google's signing keys (connection reuse)


def verify_google_credential(credential: str) -> dict:
    """Google's claims for a valid ID token issued to our client; ValueError otherwise.
    Checks signature (Google's published keys), issuer, audience (our client id) and expiry."""
    global _google_transport
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    if _google_transport is None:
        import requests

        _google_transport = google_requests.Request(session=requests.Session())
    return id_token.verify_oauth2_token(credential, _google_transport, settings.google_client_id,
                                        clock_skew_in_seconds=10)


@router.get("/google/config", response_model=GoogleConfigOut)
def google_config():
    return GoogleConfigOut(enabled=bool(settings.google_client_id), client_id=settings.google_client_id or None)


@router.post("/google", response_model=TokenResponse, dependencies=_limited)
def google_login(req: GoogleLoginRequest, request: Request, db: Session = Depends(get_db)):
    if not settings.google_client_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is not configured")
    try:
        claims = verify_google_credential(req.credential)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google sign-in") from exc
    email = claims.get("email")
    if not email or not claims.get("email_verified"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google account e-mail is not verified")
    user = db.scalar(select(User).where(User.email == _normalise_email(email)))
    created = user is None
    if created:
        user = create_user(db, email, None, claims.get("given_name") or claims.get("name"))
        user.profile.first_name = claims.get("given_name")
        user.profile.last_name = claims.get("family_name")
    return _sign_in(db, user, request, method="google", action="signup" if created else "login")


# ---------------------------------------------------------------- sessions (sign-out)

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(
    req: LogoutRequest | None = None,
    user: User = Depends(get_current_user),
    current: UserSession | None = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    """End this sign-in (its token stops working), or with ``{"all": true}`` every sign-in of the
    account. Always 204: a token that cannot be revoked (device token, dev header) has nothing to end."""
    if req and req.all:
        count = revoke_all_sessions(db, user.id)
        audit(db, user.id, "logout_all", "user", {"sessions": count})
    elif current is not None:
        revoke_session(db, current)
        audit(db, user.id, "logout", "user", {"session_id": current.id})
    db.commit()


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    user: User = Depends(get_current_user),
    current: UserSession | None = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    """Where the account is signed in right now, newest first; ``current`` marks this request's."""
    since = utcnow() - timedelta(days=settings.jwt_expire_days)
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None), UserSession.created_at > since)
        .order_by(UserSession.created_at.desc())
    ).all()
    return [SessionOut.model_validate(r).model_copy(update={"current": current is not None and r.id == current.id})
            for r in rows if session_is_active(r)]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def revoke_one_session(session_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sign out one browser / device (yours only; 404 otherwise)."""
    row = db.get(UserSession, session_id)
    if row is None or row.user_id != user.id or not session_is_active(row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    revoke_session(db, row)
    audit(db, user.id, "logout", "user", {"session_id": row.id, "remote": True})
    db.commit()


# ---------------------------------------------------------------- device tokens (iOS Shortcut)

@router.post("/device-token", response_model=DeviceTokenResponse, status_code=status.HTTP_201_CREATED)
def create_device_token(
    req: DeviceTokenRequest | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    label = req.label if req else "iOS Shortcut"
    row, raw = issue_device_token(db, user, label)
    audit(db, user.id, "create", "device_token", {"id": row.id})
    db.commit()
    return DeviceTokenResponse(id=row.id, token=raw, label=row.label)


@router.get("/device-tokens", response_model=list[DeviceTokenOut])
def list_device_tokens(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Your device tokens (never the tokens themselves, only their labels and last use)."""
    return db.scalars(select(DeviceToken).where(DeviceToken.user_id == user.id)
                      .order_by(DeviceToken.created_at.desc())).all()


@router.delete("/device-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def revoke_device_token(token_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(DeviceToken, token_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device token not found")
    db.delete(row)
    audit(db, user.id, "revoke", "device_token", {"id": token_id})
    db.commit()


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
