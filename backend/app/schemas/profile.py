import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

#: Canadian province / territory codes.
PROVINCES = {"AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
             "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
             "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon"}
SEXES = ("male", "female", "other", "prefer_not")
#: A1A 1A1 (first letter never D F I O Q U W Z; other letters never D F I O Q U).
POSTAL_RE = re.compile(r"^([ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z])[ -]?(\d[ABCEGHJ-NPRSTV-Z]\d)$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_postal_code(value: str | None) -> str | None:
    """'m5h2n2' / 'M5H-2N2' -> 'M5H 2N2'; ValueError when not a Canadian postal code."""
    if value is None or not str(value).strip():
        return None
    m = POSTAL_RE.match(str(value).strip().upper())
    if not m:
        raise ValueError("postal code must be a Canadian postal code like A1A 1A1")
    return f"{m.group(1)} {m.group(2)}"


def check_phone(value: str | None) -> str | None:
    """E.164-ish: '+1 416-555-0142', '(416) 555-0142', '+44 20 7946 0000'. Formatting is kept."""
    if value is None or not str(value).strip():
        return None
    v = str(value).strip()
    if not re.fullmatch(r"\+?[\d\s().-]{7,24}", v):
        raise ValueError("phone must be a phone number like +1 416-555-0142")
    digits = re.sub(r"\D", "", v)
    ok = (8 <= len(digits) <= 15) if v.startswith("+") else (len(digits) == 10 or (len(digits) == 11 and digits[0] == "1"))
    if not ok:
        raise ValueError("phone must be a phone number like +1 416-555-0142")
    return v


class FamilyDoctor(BaseModel):
    """The patient's family doctor (Canada: needed for specialist referrals). Not medical data."""

    name: str | None = Field(default=None, max_length=200)
    clinic: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return check_phone(v)

ConsentPurpose = Literal["data_processing", "ai_assessment", "calendar_access", "share_with_clinic", "emergency_contact"]


class ProfileBase(BaseModel):
    """Onboarding + health profile. Identity fields (legal name, DOB, sex, contact, address, family
    doctor, referral, language) are what a Canadian clinic asks when booking; the booking agent may
    share them (booking-v2 §0). There is deliberately NO health-card / OHIP / government-ID field: the
    patient brings the health card to the visit."""

    display_name: str | None = Field(default=None, max_length=200)   # preferred name
    first_name: str | None = Field(default=None, max_length=100)     # legal, as on the health card
    last_name: str | None = Field(default=None, max_length=100)
    date_of_birth: date | None = None
    sex: Literal["male", "female", "other", "prefer_not"] | None = None   # as on the health card
    height_cm: float | None = Field(default=None, gt=0, lt=300)
    weight_kg: float | None = Field(default=None, gt=0, lt=500)
    smoking_status: Literal["never", "former", "current"] | None = None
    diabetes: bool | None = None
    ckd: bool | None = None
    egfr: float | None = None
    ldl_mg_dl: float | None = None
    hdl_mg_dl: float | None = None
    total_chol_mg_dl: float | None = None
    known_hypertension: bool | None = None
    known_af: bool | None = None
    known_chd: bool | None = None
    known_pd: bool | None = None
    known_dementia: bool | None = None
    family_history: dict[str, Any] | None = None
    medications: list[dict[str, Any]] | None = None
    address: str | None = None                                     # free-form full address (optional)
    street: str | None = Field(default=None, max_length=200)       # "100 Queen St W, Unit 4"
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=16)   # Canadian, normalised to "M5H 2N2"
    province: str | None = Field(default=None, max_length=8)       # ON, BC, QC, ...
    phone: str | None = Field(default=None, max_length=32)
    contact_email: str | None = Field(default=None, max_length=320)  # optional; default = account e-mail
    lat: float | None = None
    lng: float | None = None
    language: str | None = None                                    # preferred language (en, fr, …)
    timezone: str | None = None
    family_doctor: FamilyDoctor | None = None
    has_referral: bool | None = None
    emergency_contact: dict[str, Any] | None = None


class ProfileUpdate(ProfileBase):
    """PUT /api/profile — only fields present in the body are changed. Validation (input only, so
    older stored values never break GET): DOB in the past, postal code A1A 1A1 (normalised),
    province code, E.164-ish phone numbers, e-mail format."""

    @field_validator("date_of_birth")
    @classmethod
    def _dob_in_past(cls, v: date | None) -> date | None:
        if v is not None and not (date(1900, 1, 1) <= v < date.today()):
            raise ValueError("date of birth must be in the past (after 1900)")
        return v

    @field_validator("postal_code")
    @classmethod
    def _postal(cls, v: str | None) -> str | None:
        return normalize_postal_code(v)

    @field_validator("province")
    @classmethod
    def _province(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        code = v.strip().upper()
        if code not in PROVINCES:
            raise ValueError(f"province must be one of {', '.join(PROVINCES)}")
        return code

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return check_phone(v)

    @field_validator("contact_email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        if not EMAIL_RE.match(v.strip()):
            raise ValueError("contact_email must be an e-mail address")
        return v.strip().lower()

    @field_validator("first_name", "last_name", "display_name", "street", "city")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return (v.strip() or None) if isinstance(v, str) else v


class ProfileOut(ProfileBase, ORMModel):
    user_id: str
    age: int | None = None
    family_doctor: dict[str, Any] | None = None   # stored as-is ({"name","clinic","phone"})
    language: str = "en"
    timezone: str = "America/Toronto"
    updated_at: datetime | None = None


class BookingReadinessOut(BaseModel):
    """GET /api/profile/booking-readiness. `missing` = required identity fields the booking agent
    cannot answer yet (a clinic would ask for them); `recommended` = optional ones still empty;
    `fields_shared_with_clinics` = every field a clinic may receive (on request, by phone)."""

    ready: bool
    missing: list[str]
    recommended: list[str]
    fields_shared_with_clinics: list[str]
    never_shared: list[str]


class ConsentRequest(BaseModel):
    purpose: ConsentPurpose
    granted: bool = True


class ConsentOut(ORMModel):
    id: str
    purpose: str
    granted_at: datetime
    revoked_at: datetime | None
    active: bool
