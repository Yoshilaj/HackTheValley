from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class SignupRequest(BaseModel):
    # Plain str (not EmailStr) to avoid the email-validator dependency; basic check in service.
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str


class DeviceTokenRequest(BaseModel):
    label: str | None = "iOS Shortcut"


class DeviceTokenResponse(BaseModel):
    id: str
    token: str  # shown once; only the sha256 hash is stored
    label: str | None


class UserOut(ORMModel):
    id: str
    email: str
    created_at: datetime
