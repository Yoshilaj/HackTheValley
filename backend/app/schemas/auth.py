from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class SignupRequest(BaseModel):
    # Plain str (not EmailStr) to avoid the email-validator dependency; basic check in service.
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=128)


class GoogleLoginRequest(BaseModel):
    """The ID token ("credential") Google Identity Services hands the web app after sign-in."""

    credential: str = Field(min_length=20, max_length=8000)


class GoogleConfigOut(BaseModel):
    """What the web app needs to show "Sign in with Google": the OAuth client id is public."""

    enabled: bool
    client_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str | None = None  # the account's e-mail, for the UI to show who is signed in


class LogoutRequest(BaseModel):
    all: bool = False  # true: sign out of every browser / device, not only this one


class SessionOut(ORMModel):
    """An active web sign-in (see UserSession)."""

    id: str
    method: str
    label: str | None
    created_at: datetime
    last_seen_at: datetime | None
    current: bool = False


class DeviceTokenRequest(BaseModel):
    label: str | None = Field(default="iOS Shortcut", max_length=100)


class DeviceTokenResponse(BaseModel):
    id: str
    token: str  # shown once; only the sha256 hash is stored
    label: str | None


class DeviceTokenOut(ORMModel):
    id: str
    label: str | None
    created_at: datetime
    last_used_at: datetime | None


class UserOut(ORMModel):
    id: str
    email: str
    created_at: datetime
