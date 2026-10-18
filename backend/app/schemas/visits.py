"""Clinic directory, follow-up recommendations and visits the user books themselves."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProviderOut(BaseModel):
    id: str
    type: str                       # clinic | hospital
    name: str
    specialty: str                  # display label, e.g. "Cardiology"
    clinic: str | None = None
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    hours: str | None = None        # e.g. "Mon-Fri 8:30 AM-4:30 PM"
    distance: str | None = None     # e.g. "2.2 km" (needs the user's or profile location)
    distance_km: float | None = None
    accepting_new: bool | None = None
    languages: list[str] = Field(default_factory=list)
    glyph: str
    slots: list[str] = Field(default_factory=list)   # local ISO times, e.g. "2026-10-21T10:00"
    next_slot: str                  # e.g. "Tue, Oct 21 · 10:00 AM"
    fictional: bool = False
    # What distance_km is measured from, in a straight line: "device" (the location the app sent) |
    # "home" (the profile address) | None (no distance)
    distance_from: str | None = None


class RecommendationOut(ProviderOut):
    reason: str


class BookingCreate(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    slot: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
    note: str | None = Field(default=None, max_length=1000)
    # Details to remember when the provider isn't in the backend's directory.
    provider: dict[str, Any] | None = None


class BookingOut(BaseModel):
    id: str
    provider_id: str
    provider: dict[str, Any]
    slot: str | None = None
    slot_label: str
    note: str | None = None
    status: str
    created_at: datetime
