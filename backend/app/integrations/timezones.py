"""Clinic time zones for Canada (booking-v2 spec §1.3: slots are in the clinic's time zone).

    tz = infer_timezone(address="1 Main St, Vancouver, BC V6B 1A1, Canada", utc_offset_minutes=-420)
    tz = resolve_timezone(clinic, fallback="America/Toronto")   # the ONE fallback chain used everywhere

Order: an explicit valid `timezone` → the province / territory in the address (two-letter code,
full name, or the postal-code first letter) → a Canadian zone whose current UTC offset equals
Places' `utcOffsetMinutes` → `fallback` (the user's zone) → America/Toronto. When the province
zone disagrees with `utcOffsetMinutes` (e.g. NW Ontario on Central time) a Canadian zone that
matches the offset wins. Ontario / Quebec sub-zone exceptions are otherwise ignored.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Toronto"
PROVINCE_TZ = {
    "BC": "America/Vancouver", "AB": "America/Edmonton", "SK": "America/Regina", "MB": "America/Winnipeg",
    "ON": "America/Toronto", "QC": "America/Toronto", "NB": "America/Halifax", "NS": "America/Halifax",
    "PE": "America/Halifax", "NL": "America/St_Johns", "YT": "America/Whitehorse", "NT": "America/Yellowknife",
    "NU": "America/Iqaluit",
}
PROVINCE_NAMES = {
    "british columbia": "BC", "alberta": "AB", "saskatchewan": "SK", "manitoba": "MB", "ontario": "ON",
    "quebec": "QC", "québec": "QC", "new brunswick": "NB", "nova scotia": "NS", "prince edward island": "PE",
    "newfoundland": "NL", "yukon": "YT", "northwest territories": "NT", "nunavut": "NU",
}
#: Canadian postal-code first letter → province (X = NT/NU: NT assumed)
POSTAL_PROVINCE = {"A": "NL", "B": "NS", "C": "PE", "E": "NB", "G": "QC", "H": "QC", "J": "QC", "K": "ON",
                   "L": "ON", "M": "ON", "N": "ON", "P": "ON", "R": "MB", "S": "SK", "T": "AB", "V": "BC",
                   "X": "NT", "Y": "YT"}
#: zones tried for an offset-only match (most populous first)
OFFSET_ZONES = ("America/Toronto", "America/Vancouver", "America/Edmonton", "America/Winnipeg", "America/Halifax",
                "America/Regina", "America/St_Johns", "America/Whitehorse")
_CODE_RE = re.compile(r"(?:^|,)\s*(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b(?:\s+[A-Z]\d[A-Z])?", re.I)
_POSTAL_RE = re.compile(r"\b([ABCEGHJKLMNPRSTVXY])\d[A-Z]\s?\d[A-Z]\d\b", re.I)


def valid_zone(tz: Any) -> str | None:
    if not tz or not isinstance(tz, str):
        return None
    try:
        ZoneInfo(tz)
        return tz
    except Exception:
        return None


def province_from_address(address: str | None, components: list[dict[str, Any]] | None = None) -> str | None:
    for c in components or []:
        if "administrative_area_level_1" in (c.get("types") or []):
            code = str(c.get("shortText") or c.get("short_name") or "").upper()
            if code in PROVINCE_TZ:
                return code
    a = address or ""
    for m in _CODE_RE.finditer(a):
        return m.group(1).upper()
    low = a.lower()
    for name, code in PROVINCE_NAMES.items():
        if name in low:
            return code
    m = _POSTAL_RE.search(a)
    return POSTAL_PROVINCE.get(m.group(1).upper()) if m else None


def _offset_min(tz: str, now: datetime) -> int:
    off = now.astimezone(ZoneInfo(tz)).utcoffset()
    return int(off.total_seconds() // 60) if off is not None else 0


def zone_for_offset(offset_minutes: Any, now: datetime | None = None) -> str | None:
    try:
        off = int(offset_minutes)
    except (TypeError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    return next((z for z in OFFSET_ZONES if _offset_min(z, now) == off), None)


def infer_timezone(address: str | None = None, utc_offset_minutes: Any = None,
                   components: list[dict[str, Any]] | None = None, now: datetime | None = None) -> str | None:
    """IANA zone from the address province, checked against utcOffsetMinutes (see module docstring)."""
    prov = province_from_address(address, components)
    by_prov = PROVINCE_TZ.get(prov) if prov else None
    by_off = zone_for_offset(utc_offset_minutes, now) if utc_offset_minutes is not None else None
    if by_prov and by_off and _offset_min(by_prov, now or datetime.now(timezone.utc)) != int(utc_offset_minutes):
        return by_off
    return by_prov or by_off


def resolve_timezone(clinic: Any, fallback: str | None = None) -> str:
    """The clinic's IANA zone (Clinic or dict) — the single fallback chain used by every caller."""
    get = (lambda k: clinic.get(k)) if isinstance(clinic, dict) else (lambda k: getattr(clinic, k, None))
    tz = valid_zone(get("timezone")) if clinic is not None else None
    if tz:
        return tz
    if clinic is not None:
        extra = get("extra") or {}
        tz = valid_zone(infer_timezone(get("address"), extra.get("utc_offset_minutes")))
        if tz:
            return tz
    return valid_zone(fallback) or DEFAULT_TZ
