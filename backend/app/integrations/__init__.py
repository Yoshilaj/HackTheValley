"""Third-party integrations (Google OAuth / Calendar / Places) and their offline fakes.

Shared value types live here so services and fakes agree on shapes:

    Clinic  - a bookable clinic (seed list or Google Places result). Booking v2 adds
              website, opening_hours ({"mon": [["09:00","17:00"]], ...} in `timezone`),
              kind (specialist | family | walk_in | hospital), rating, business_status.
    Slot    - a tz-aware [start, end) appointment slot

Factories (`get_oauth_client`, `get_calendar_client`, `get_places_client`) return the real
client only when the relevant keys / stored credentials exist; otherwise a fake. Tests never
touch the network.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Clinic:
    id: str
    name: str
    # cardiology | neurology | memory_clinic | geriatrics | family_medicine | walk_in | hospital
    specialty: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    email: str | None = None
    phone: str | None = None
    hours: str | None = None
    source: str = "seed"  # seed | google_places | fake
    place_id: str | None = None
    fictional: bool = False
    distance_km: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    website: str | None = None
    opening_hours: dict[str, list[list[str]]] | None = None   # weekday -> [[open, close]] local time
    timezone: str | None = None                               # IANA, e.g. America/Toronto
    kind: str | None = None                                   # specialist | family | walk_in | hospital
    rating: float | None = None
    business_status: str | None = None                        # OPERATIONAL | CLOSED_TEMPORARILY | ...
    specialty_match: bool = False                             # matched the pathway's primary query

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["distance_km"] is not None:
            d["distance_km"] = round(d["distance_km"], 2)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Clinic":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime

    def as_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    @classmethod
    def from_any(cls, value: Any, default_minutes: int = 30) -> "Slot":
        """Accept a Slot, {"start","end"?} dict or an ISO start string (tz-aware required)."""
        from datetime import timedelta

        if isinstance(value, Slot):
            return value
        if isinstance(value, dict):
            start = datetime.fromisoformat(str(value["start"]))
            end = datetime.fromisoformat(str(value["end"])) if value.get("end") else start + timedelta(minutes=default_minutes)
        else:
            start = datetime.fromisoformat(str(value))
            end = start + timedelta(minutes=default_minutes)
        if start.tzinfo is None:
            raise ValueError(f"slot start must include a timezone offset: {value!r}")
        return cls(start=start, end=end)
