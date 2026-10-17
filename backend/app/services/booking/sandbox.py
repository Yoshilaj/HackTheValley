"""Where outbound booking actions really go (booking-v2 spec §0 "Sandbox by default").

    target = resolve_target("email", clinic)     # Target | None (None = channel has no target)
    target.value        # address / E.164 number / URL to actually contact
    target.real         # the clinic's real detail (shown to the user, logged)
    target.redirected   # True in sandbox mode when value != real
    target.note         # "sandbox: redirected from …" for agent_log / channel_attempts

BOOKING_TARGET_MODE=sandbox (default):
  email -> SANDBOX_CLINIC_EMAIL, else CLINIC_FALLBACK_EMAIL, else the clinic's own address only if
           it is on a reserved test domain (example.com/.org/.net, *.test, *.invalid, *.example —
           undeliverable by RFC 2606, used by the fictional seed clinics); otherwise None.
  phone -> SANDBOX_CLINIC_PHONE, else None (fictional 555-01xx numbers are never dialled).
  web   -> SANDBOX_CLINIC_WEBSITE, else the built-in mock clinic `BASE_URL/mock-clinic`; a clinic
           website that already is on the mock clinic (seed clinics) keeps its variant path.
BOOKING_TARGET_MODE=live: the clinic's real details.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import settings

RESERVED_EMAIL_SUFFIXES = (".example.com", "@example.com", ".example.org", "@example.org", ".example.net",
                           "@example.net", ".test", ".invalid", ".example", ".localhost")
CHANNELS = ("web", "email", "phone")


@dataclass
class Target:
    channel: str
    value: str
    real: str | None
    redirected: bool
    mode: str
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get(clinic: Any, key: str) -> Any:
    return clinic.get(key) if isinstance(clinic, dict) else getattr(clinic, key, None)


def is_reserved_email(email: str | None) -> bool:
    e = (email or "").strip().lower()
    return bool(e) and e.endswith(RESERVED_EMAIL_SUFFIXES)


def mock_clinic_base() -> str:
    return settings.base_url.rstrip("/") + "/mock-clinic"


def real_detail(channel: str, clinic: Any) -> str | None:
    if channel == "email":
        return _get(clinic, "email") or (_get(clinic, "extra") or {}).get("detected_email")
    if channel == "phone":
        return (_get(clinic, "extra") or {}).get("international_phone") or _get(clinic, "phone")
    if channel == "web":
        return (_get(clinic, "extra") or {}).get("form_url") or _get(clinic, "website")
    raise ValueError(f"unknown channel {channel!r}")


def resolve_target(channel: str, clinic: Any, mode: str | None = None) -> Target | None:
    """The address/number/URL to actually use for `channel` (see module docstring)."""
    mode = mode or settings.booking_target_mode
    real = real_detail(channel, clinic)
    if mode == "live":
        return Target(channel, real, real, False, mode) if real else None

    value: str | None = None
    if channel == "email":
        if not real:
            return None  # a clinic without an address has no e-mail channel, even in sandbox
        value = settings.sandbox_clinic_email or settings.clinic_fallback_email or (real if is_reserved_email(real) else None)
    elif channel == "phone":
        if not real:
            return None
        value = settings.sandbox_clinic_phone or None
    elif channel == "web":
        if not real:
            return None
        if settings.sandbox_clinic_website:
            value = settings.sandbox_clinic_website
        elif real.startswith(mock_clinic_base()):
            value = real
        else:
            value = mock_clinic_base()
    if not value:
        return None
    redirected = value != real
    note = f"sandbox: redirected from {real}" if redirected else None
    return Target(channel, value, real, redirected, mode, note)


def missing_reason(channel: str, clinic: Any) -> str:
    """Why resolve_target returned None (for channel_attempts)."""
    if not real_detail(channel, clinic):
        return {"email": "no e-mail address", "phone": "no phone number", "web": "no website"}[channel]
    if settings.booking_target_mode != "live":
        return {"email": "sandbox: SANDBOX_CLINIC_EMAIL not set", "phone": "sandbox: SANDBOX_CLINIC_PHONE not set",
                "web": "sandbox: no sandbox website"}[channel]
    return "no target"
