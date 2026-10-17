"""Channel contract shared by web / email / phone (booking-v2 spec §1.4–1.6).

A channel is any object with:

    name: str                                                   # "web" | "email" | "phone"
    configured() -> bool                                        # provider/settings present at all
    available(clinic: Clinic, ctx: BookingContext) -> str | None   # None = usable, else the reason
    execute(appt: Appointment, ctx: BookingContext) -> ChannelResult

`available` must be cheap and side-effect free (it is called while planning, before the user
approves, and again right before `execute`). It returns a short human reason when the channel
cannot be used for this clinic (e.g. "not configured", "login required", "no e-mail address",
"sandbox: SANDBOX_CLINIC_PHONE not set"); blocked channels are skipped instantly and recorded in
`Appointment.channel_attempts` — the agent never asks the user to log in anywhere.

`execute` performs exactly ONE outbound contact attempt and returns a ChannelResult:
    status  requested  - the request reached the clinic; a reply / call-back / confirmation follows
            confirmed  - the clinic confirmed a slot right away (`slot` = {"start","end"} ISO)
            skipped    - nothing was sent (blocked at the last moment, not configured, …)
            failed     - an attempt was made and failed (bounce, call failed, submit error)
    reason        short text for channel_attempts / agent_log
    external_ref  provider id (Resend message id, ElevenLabs conversation id, web confirmation no.)
    messages      [ChannelMessage] — the orchestrator persists them as AppointmentMessage rows
                  (idempotent on external_id); channels never write AppointmentMessage themselves
    slot          confirmed slot (only for status confirmed)
    target        sandbox.Target.as_dict() actually used (real detail kept for display/log)
    details       anything else worth logging (JSON-serialisable; no raw health data)

Channels may set `appt.reply_token` / `appt.external_ref`, but status transitions, calendar
events, notifications and channel_attempts are the orchestrator's job
(app/services/booking/orchestrator.py). Only the fields in `ALLOWED_FIELDS` (see
`PatientInfo.shared_fields()`) may leave the system — never raw measurements, diagnoses or
health-card numbers. Asynchronous outcomes (inbound e-mail, phone post-call webhook) go through
`orchestrator.apply_outcome(db, appt, outcome, channel=..., message=...)`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from app.integrations import Clinic, Slot

ChannelStatus = Literal["requested", "confirmed", "skipped", "failed"]
CHANNEL_ORDER = ("web", "email", "phone")
#: The only patient fields any channel may share with a clinic (spec §0 minimal disclosure): the
#: identity / contact details a Canadian clinic routinely asks when booking, the generic reason
#: category, the proposed times and the report link. NEVER: health-card / OHIP / government-ID numbers,
#: conditions, measurements, medications, diagnoses (the patient brings the health card to the visit).
#: Each channel shares a subset: the phone agent answers identity questions only when asked, the
#: e-mail carries name + DOB + contact + postal code, the web form fills only the fields it asks for.
ALLOWED_FIELDS = ("name", "first_name", "last_name", "date_of_birth", "sex", "phone", "email", "postal_code",
                  "address", "street", "city", "province", "family_doctor_name", "family_doctor_clinic",
                  "family_doctor_phone", "has_referral", "preferred_language", "reason_category",
                  "preferred_slots", "report_url")
#: Never part of PatientInfo / any clinic-facing payload (asserted in tests).
NEVER_SHARED = ("health_card_number", "government_id", "conditions", "measurements", "medications", "diagnoses",
                "insurance")


@dataclass
class PatientInfo:
    name: str                            # legal "First Last" when known, else the preferred name
    date_of_birth: str | None = None     # ISO date
    phone: str | None = None
    email: str | None = None
    postal_code: str | None = None
    address: str | None = None           # full one-line address ("100 Queen St W, Toronto, ON M5H 2N2")
    reason_category: str = ""
    preferred_slots: list[str] = field(default_factory=list)   # human labels in the clinic's timezone
    report_url: str | None = None
    first_name: str | None = None        # legal name parts
    last_name: str | None = None
    sex: str | None = None               # male | female | other | prefer_not (as on the health card)
    street: str | None = None
    city: str | None = None
    province: str | None = None          # ON, BC, …
    family_doctor_name: str | None = None
    family_doctor_clinic: str | None = None
    family_doctor_phone: str | None = None
    has_referral: bool | None = None
    preferred_language: str | None = None   # "English", "French", …

    def shared_fields(self) -> dict[str, Any]:
        """Every allow-listed field that has a value (False counts as a value for has_referral)."""
        d = asdict(self)
        return {k: d[k] for k in ALLOWED_FIELDS if d.get(k) not in (None, "", [])}


@dataclass
class ChannelMessage:
    direction: str                        # out | in
    channel: str                          # email | phone | web
    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None
    body: str | None = None
    external_id: str | None = None
    classification: dict[str, Any] | None = None


@dataclass
class ChannelResult:
    status: ChannelStatus
    reason: str | None = None
    external_ref: str | None = None
    messages: list[ChannelMessage] = field(default_factory=list)
    slot: dict[str, str] | None = None
    target: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def skipped(cls, reason: str, **kw: Any) -> "ChannelResult":
        return cls(status="skipped", reason=reason, **kw)

    @classmethod
    def failed(cls, reason: str, **kw: Any) -> "ChannelResult":
        return cls(status="failed", reason=reason, **kw)

    @property
    def ok(self) -> bool:
        return self.status in ("requested", "confirmed")


@dataclass
class BookingContext:
    """Everything a channel needs for one clinic. Built by the orchestrator."""

    db: Any
    user: Any
    appointment: Any
    clinic: Clinic
    slots: list[Slot]
    clinic_tz: str
    user_tz: str
    patient: PatientInfo
    report_url: str
    llm: Any = None
    calendar: Any = None
    email: Any = None                     # EmailProvider for clinic-facing mail
    now: datetime | None = None
    detection: dict[str, Any] | None = None   # channels.detect.DetectionResult.as_dict() or None
    use_freebusy: bool = False
    calendar_events_allowed: bool = False
    pathway: str = "general"
    reminder: bool = False                # follow-up job re-sending on the same channel
    providers: dict[str, Any] = field(default_factory=dict)   # e.g. {"phone_caller": …, "browser": …}

    def slot_dicts(self) -> list[dict[str, str]]:
        return [s.as_dict() for s in self.slots]


class Channel(Protocol):
    name: str

    def configured(self) -> bool: ...

    def available(self, clinic: Clinic, ctx: BookingContext) -> str | None: ...

    def execute(self, appt: Any, ctx: BookingContext) -> ChannelResult: ...
