from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import create_access_token, get_current_user
from app.core.config import settings
from app.core.security import audit, hash_password, new_device_token, sha256_hex, verify_password
from app.db.models import DeviceToken, Profile, User
from app.db.session import get_db
from app.schemas.auth import (
    DeviceTokenRequest,
    DeviceTokenResponse,
    GoogleConfigOut,
    GoogleLoginRequest,
    LoginRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


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
    return user if user and verify_password(password, user.password_hash) else None


def issue_device_token(db: Session, user: User, label: str | None) -> tuple[DeviceToken, str]:
    raw = new_device_token()
    row = DeviceToken(user_id=user.id, token_hash=sha256_hex(raw), label=label)
    db.add(row)
    db.flush()
    return row, raw


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.email == _normalise_email(req.email))):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = create_user(db, req.email, req.password, req.display_name)
    audit(db, user.id, "signup", "user")
    db.commit()
    return TokenResponse(access_token=create_access_token(user.id), user_id=user.id)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, req.email, req.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    audit(db, user.id, "login", "user", commit=True)
    return TokenResponse(access_token=create_access_token(user.id), user_id=user.id)


# ---------------------------------------------------------------- Sign in with Google
# The web app shows Google Identity Services (button + One Tap) with the public client id from
# /google/config, gets an ID token, and posts it here. The token is verified against Google's keys
# and GOOGLE_CLIENT_ID; the account is the one with that (Google-verified) e-mail, created on first
# sign-in. Separate from /api/integrations/google/*, which connects Google Calendar to an account.

def verify_google_credential(credential: str) -> dict:
    """Google's claims for a valid ID token issued to our client; ValueError otherwise."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(credential, google_requests.Request(), settings.google_client_id)


@router.get("/google/config", response_model=GoogleConfigOut)
def google_config():
    return GoogleConfigOut(enabled=bool(settings.google_client_id), client_id=settings.google_client_id or None)


@router.post("/google", response_model=TokenResponse)
def google_login(req: GoogleLoginRequest, db: Session = Depends(get_db)):
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
    audit(db, user.id, "signup" if created else "login", "user", {"method": "google"})
    db.commit()
    return TokenResponse(access_token=create_access_token(user.id), user_id=user.id)


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


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
