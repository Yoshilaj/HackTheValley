"""Medications, refill requests and daily dose tick-offs (the web app's Medications page)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class MedicationIn(BaseModel):
    """One entry in ``Profile.medications``. Only ``name`` is required; ``times`` are the day's dose
    times as shown to the user ("8:00 AM"), used for the daily schedule and its tick-offs."""

    name: str = Field(min_length=1, max_length=200)
    dose: str | None = Field(default=None, max_length=100)
    frequency: str | None = Field(default=None, max_length=100)
    purpose: str | None = Field(default=None, max_length=200)
    prescriber: str | None = Field(default=None, max_length=200)
    refills: int | None = Field(default=None, ge=0, le=99)
    times: list[str] = Field(default_factory=list, max_length=12)
    days_left: int | None = Field(default=None, ge=0, le=3650)
    take_with: str | None = Field(default=None, max_length=300)
    side_effects: list[str] = Field(default_factory=list, max_length=30)
    interactions: list[str] = Field(default_factory=list, max_length=30)
    glyph: str | None = Field(default=None, max_length=4)


class MedicationUpdate(BaseModel):
    """PATCH: only the fields sent change."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    dose: str | None = Field(default=None, max_length=100)
    frequency: str | None = Field(default=None, max_length=100)
    purpose: str | None = Field(default=None, max_length=200)
    prescriber: str | None = Field(default=None, max_length=200)
    refills: int | None = Field(default=None, ge=0, le=99)
    times: list[str] | None = Field(default=None, max_length=12)
    days_left: int | None = Field(default=None, ge=0, le=3650)
    take_with: str | None = Field(default=None, max_length=300)
    side_effects: list[str] | None = Field(default=None, max_length=30)
    interactions: list[str] | None = Field(default=None, max_length=30)
    glyph: str | None = Field(default=None, max_length=4)


class MedicationOut(MedicationIn):
    id: str


class RefillCreate(BaseModel):
    medication_ids: list[str] = Field(min_length=1, max_length=50)
    pharmacy_id: str | None = Field(default=None, max_length=64)
    method: Literal["pickup", "delivery"] = "pickup"
    note: str | None = Field(default=None, max_length=1000)


class RefillOut(ORMModel):
    id: str
    medication_id: str
    pharmacy_id: str | None = None
    method: str
    note: str | None = None
    status: str
    created_at: datetime
    cancelled_at: datetime | None = None


class DosesIn(BaseModel):
    """The full set of doses ticked off that day (replaces what was saved)."""

    taken: list[str] = Field(default_factory=list, max_length=100)


class DosesOut(BaseModel):
    day: date
    taken: list[str]
