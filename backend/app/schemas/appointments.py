"""Schemas for appointments (booking agent v2) and integrations status."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import ORMModel

_SLOTS = [{"start": "2026-10-20T10:00:00-04:00", "end": "2026-10-20T10:30:00-04:00"},
          {"start": "2026-10-22T14:00:00-04:00", "end": "2026-10-22T14:30:00-04:00"},
          {"start": "2026-10-24T11:00:00-04:00", "end": "2026-10-24T11:30:00-04:00"}]
_EXAMPLE = {
    "id": "a1b2…", "assessment_id": "9b1e…", "status": "awaiting_approval", "channel": None,
    "clinic": {"id": "seed_bloor_heart", "name": "Bloor Street Heart Clinic (demo)", "specialty": "cardiology",
               "kind": "specialist", "address": "401 Bloor St W, Toronto, ON M5S 1X5 (fictional)", "distance_km": 1.2,
               "phone": "+1 416-555-0101", "email": "booking@bloorheart.example.com",
               "website": "http://localhost:8000/mock-clinic", "timezone": "America/Toronto",
               "opening_hours": {"mon": [["08:30", "16:30"]]}, "source": "seed"},
    "proposed_slots": _SLOTS, "chosen_slot": None, "calendar_event_id": None,
    "approval": {"status": "pending", "requested_at": "2026-10-17T09:00:05Z",
                 "clinic": {"id": "seed_bloor_heart", "name": "Bloor Street Heart Clinic (demo)", "distance_km": 1.2},
                 "channel_plan": [
                     {"channel": "web", "available": False, "reason": "not configured", "target": None,
                      "real": "http://localhost:8000/mock-clinic", "redirected": False},
                     {"channel": "email", "available": True, "reason": None, "target": "inbox@team.example.org",
                      "real": "booking@bloorheart.example.com", "redirected": True},
                     {"channel": "phone", "available": False, "reason": "not configured", "target": None,
                      "real": "+1 416-555-0101", "redirected": False}],
                 "slots": [s | {"label": "Mon Oct 20, 2026, 10:00 AM–10:30 AM EDT"} for s in _SLOTS[:1]],
                 "shared_fields": {"name": "Alex Tremblay", "date_of_birth": "1958-01-15", "phone": "+1 416-555-0199",
                                   "email": "alex@example.com", "postal_code": "M5S 1X5",
                                   "reason_category": "Heart and blood-pressure check-up",
                                   "preferred_slots": ["Mon Oct 20, 2026, 10:00 AM–10:30 AM EDT"],
                                   "report_url": "http://localhost:8000/r/Xk3…"},
                 "missing_profile_fields": ["first_name", "last_name"],
                 "referral_note": "In Canada, specialists usually need a referral …", "target_mode": "sandbox"},
    "channel_attempts": [], "external_ref": None, "pending_question": None,
    "created_at": "2026-10-17T09:00:00Z", "updated_at": "2026-10-17T09:00:05Z",
}


class AppointmentOut(ORMModel):
    """`status` ∈ proposed | awaiting_approval | processing (transient) | requested | needs_user_info |
    confirmed | declined | cancelled | failed. `approval` is the approval card; `pending_question` is set while
    `needs_user_info`; `channel_attempts` = [{ts, clinic_id, channel, status: requested|confirmed|
    skipped|failed|declined, reason, external_ref, target, redirected}]."""

    id: str
    assessment_id: str | None = None
    status: str
    channel: str | None = None
    clinic: dict[str, Any] | None = None
    proposed_slots: list[dict[str, Any]] | None = None
    chosen_slot: dict[str, Any] | None = None
    calendar_event_id: str | None = None
    approval: dict[str, Any] | None = None
    channel_attempts: list[dict[str, Any]] | None = None
    external_ref: str | None = None
    pending_question: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, json_schema_extra={"example": _EXAMPLE})

    @model_validator(mode="after")
    def _pending_question(self):
        if self.pending_question is None and isinstance(self.approval, dict):
            self.pending_question = self.approval.get("pending_question")
        return self


class AppointmentDetailOut(AppointmentOut):
    """Adds `candidates` (ranked clinics considered) and `agent_log` (what the agent did): entries
    `{ts, type: note|tool|discovery|approval_requested|approved|channel|calendar_event|clinic_reply|
    needs_user_info|user_info|reply|confirmed|next_candidate|reminder|next_channel|failed|cancelled|
    calendar_update…, …}`."""

    candidates: list[dict[str, Any]] | None = None
    agent_log: list[dict[str, Any]] | None = None

    model_config = ConfigDict(from_attributes=True, json_schema_extra={"example": _EXAMPLE | {
        "candidates": [{"id": "seed_bloor_heart", "name": "Bloor Street Heart Clinic (demo)", "distance_km": 1.2}],
        "agent_log": [
            {"ts": "2026-10-17T09:00:00Z", "type": "note", "text": "booking agent started"},
            {"ts": "2026-10-17T09:00:01Z", "type": "tool", "tool": "find_clinics", "mode": "llm", "input": {},
             "output": {"clinics": ["…"]}, "is_error": False},
        ]}})


class AppointmentMessageOut(ORMModel):
    id: str
    direction: str
    channel: str
    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    body: str | None = None
    classification: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, json_schema_extra={"example": {
        "id": "m1…", "direction": "in", "channel": "email", "sender": "inbox@team.example.org",
        "recipient": "book+3f9c…@book.example.com", "subject": "Re: Appointment request for Alex Tremblay",
        "body": "Option 2 works, see you then.", "created_at": "2026-10-18T13:02:00Z",
        "classification": {"outcome": "confirmed", "slot": "2026-10-22T14:00:00-04:00", "requested_info": "",
                           "summary": "Option 2 works, see you then.", "method": "llm"}}})


class UserInfoIn(BaseModel):
    """Answer to the clinic (status needs_user_info). Counter-offer: `accept` true/false (+ optional
    `answer`). Question: `answer` (never a health-card number — rejected with 422)."""

    answer: str | None = Field(None, max_length=1000)
    accept: bool | None = None

    model_config = ConfigDict(json_schema_extra={"example": {"answer": "Yes, I have a referral from Dr. Chen."}})


class GoogleStartOut(BaseModel):
    auth_url: str
    state: str
    fake: bool

    model_config = ConfigDict(json_schema_extra={"example": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=…&scope=…", "state": "uid.1760000000.ab12…",
        "fake": False}})


class IntegrationsStatusOut(BaseModel):
    google: dict[str, Any]
    places: dict[str, Any]
    email: dict[str, Any]

    model_config = ConfigDict(json_schema_extra={"example": {
        "google": {"configured": True, "connected": True, "fake": False,
                   "scopes": ["https://www.googleapis.com/auth/calendar.events"],
                   "expires_at": "2026-10-17T10:00:00Z", "calendar_consent": True},
        "places": {"configured": False, "source": "seed"},
        "email": {"provider": "fake", "clinic_fallback_email": False}}})
