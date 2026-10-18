"""All SQLAlchemy models (one file, grouped). Derived from docs/BACKEND_PLAN.md Phase B
and docs/specs/phase-a-assessments.md.

Conventions:
- UUID string primary keys (`id`), tz-aware UTC datetimes, JSON via sqlalchemy.JSON.
- Enum-like columns are plain strings; allowed values are the tuples below
  (validated in schemas/services, not by the DB, so they are cheap to extend).
- Every user-owned table has `user_id` FK -> users.id ON DELETE CASCADE and is listed
  in USER_OWNED_MODELS (used by DELETE /api/me and GET /api/me/export).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, IdMixin, utcnow
from app.db.types import EMBEDDING_DIM, VectorType

# ---------------------------------------------------------------- enum values
CONSENT_PURPOSES = ("data_processing", "ai_assessment", "calendar_access", "share_with_clinic", "emergency_contact")
METRIC_SOURCES = ("apple_health", "shortcut", "fitbit", "manual", "omron_photo")
BP_SESSIONS = ("morning", "evening", "other")
RHYTHM_SOURCES = ("apple_irn", "apple_ecg", "omron_connect", "manual_irregular_pulse")
RHYTHM_LABELS = (
    "sinus_rhythm", "possible_af", "afib", "high_hr", "low_hr", "inconclusive",
    "poor_recording", "irregular_rhythm_notification", "other",
)
OBSERVATION_KINDS = ("symptom", "functional_change", "medication", "lifestyle", "mood", "red_flag")
OBSERVATION_SOURCES = ("chat", "log", "document", "screener")
TASK_TYPES = (
    "story_recall", "sustained_vowel", "word_recall", "finger_tap",
    "picture_description", "free_speech", "fluency",
)
PATHWAYS = ("cognitive", "cardio", "parkinsons", "general")
RISK_LEVELS = ("insufficient_data", "invalid_measurement", "no_finding", "watch", "followup_recommended", "emergency")
APPOINTMENT_STATUSES = (
    "proposed", "awaiting_approval", "processing", "requested", "needs_user_info", "confirmed", "declined", "cancelled",
    "failed",
)  # processing = transient atomic claim while an approval / answer / confirm runs


def user_fk(nullable: bool = False, index: bool = True):
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=nullable, index=index)


JsonDict = dict[str, Any]


# ================================================================ Identity / consent

class User(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))

    profile: Mapped["Profile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )
    consents: Mapped[list["Consent"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    device_tokens: Mapped[list["DeviceToken"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class Profile(IdMixin, Base):
    __tablename__ = "profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))   # preferred name (app greeting)
    # legal name as on the health card — what a clinic books under (booking-v2 §0 identity fields)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    date_of_birth: Mapped[date | None]
    sex: Mapped[str | None] = mapped_column(String(16))  # as on the health card: male | female | other | prefer_not
    height_cm: Mapped[float | None]
    weight_kg: Mapped[float | None]
    smoking_status: Mapped[str | None] = mapped_column(String(16))  # never | former | current
    diabetes: Mapped[bool | None]
    ckd: Mapped[bool | None]
    egfr: Mapped[float | None]
    ldl_mg_dl: Mapped[float | None]
    hdl_mg_dl: Mapped[float | None]
    total_chol_mg_dl: Mapped[float | None]
    known_hypertension: Mapped[bool | None]
    known_af: Mapped[bool | None]
    known_chd: Mapped[bool | None]
    known_pd: Mapped[bool | None]
    known_dementia: Mapped[bool | None]
    family_history: Mapped[JsonDict | None] = mapped_column(JSON)   # {"dementia": true, ...}
    medications: Mapped[list | None] = mapped_column(JSON)          # [{"name","dose","schedule"}]
    # What the user lists on their health profile in the web app (free text, as they write it).
    # Separate from the known_* flags, which the risk rules read.
    allergies: Mapped[list | None] = mapped_column(JSON)            # ["Penicillin", ...]
    conditions: Mapped[list | None] = mapped_column(JSON)           # ["Prediabetes", ...]
    address: Mapped[str | None] = mapped_column(Text)          # free-form full address (geocoding); kept in sync with street/city
    street: Mapped[str | None] = mapped_column(String(200))    # "100 Queen St W" (incl. unit)
    city: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(16))
    province: Mapped[str | None] = mapped_column(String(8))   # ON, BC, ...
    phone: Mapped[str | None] = mapped_column(String(32))     # patient contact number given to clinics
    contact_email: Mapped[str | None] = mapped_column(String(320))  # optional; clinics get this, else User.email
    family_doctor: Mapped[JsonDict | None] = mapped_column(JSON)    # {"name","clinic","phone"} (referrals)
    has_referral: Mapped[bool | None]                               # already holds a referral (optional)
    lat: Mapped[float | None]
    lng: Mapped[float | None]
    language: Mapped[str] = mapped_column(String(8), default="en")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Toronto")
    emergency_contact: Mapped[JsonDict | None] = mapped_column(JSON)  # {"name","phone","email","relation"}
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="profile")

    @property
    def age(self) -> int | None:
        if not self.date_of_birth:
            return None
        today = date.today()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def legal_name(self) -> str | None:
        """'First Last' when both legal name parts are set, else None."""
        parts = [(self.first_name or "").strip(), (self.last_name or "").strip()]
        return " ".join(parts) if all(parts) else None

    @property
    def full_address(self) -> str | None:
        """'100 Queen St W, Toronto, ON M5H 2N2' from the parts, else the free-form `address`."""
        street, city = (self.street or "").strip(), (self.city or "").strip()
        if not street:
            return (self.address or "").strip() or None
        tail = " ".join(x for x in ((self.province or "").strip(), (self.postal_code or "").strip()) if x)
        return ", ".join(x for x in (street, city, tail) if x)


class Consent(IdMixin, Base):
    __tablename__ = "consents"

    user_id: Mapped[str] = user_fk()
    purpose: Mapped[str] = mapped_column(String(32))  # CONSENT_PURPOSES
    granted_at: Mapped[datetime] = mapped_column(default=utcnow)
    revoked_at: Mapped[datetime | None]

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class DeviceToken(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "device_tokens"

    user_id: Mapped[str] = user_fk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256 hex
    label: Mapped[str | None] = mapped_column(String(100))
    last_used_at: Mapped[datetime | None]


# ================================================================ Health data

class MetricSample(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "metric_samples"
    __table_args__ = (
        UniqueConstraint("user_id", "type", "recorded_at", "source", name="uq_metric_sample"),
        Index("ix_metric_samples_user_type_time", "user_id", "type", "recorded_at"),
    )

    user_id: Mapped[str] = user_fk(index=False)
    type: Mapped[str] = mapped_column(String(64))  # e.g. resting_hr, hrv_sdnn, steps, walking_speed, spo2
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    recorded_at: Mapped[datetime]
    source: Mapped[str] = mapped_column(String(32), default="manual")  # METRIC_SOURCES
    meta: Mapped[JsonDict | None] = mapped_column(JSON)


class BpReading(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "bp_readings"
    __table_args__ = (Index("ix_bp_readings_user_time", "user_id", "measured_at"),)

    user_id: Mapped[str] = user_fk(index=False)
    session: Mapped[str] = mapped_column(String(16), default="other")  # BP_SESSIONS
    reading_no: Mapped[int] = mapped_column(default=1)
    systolic: Mapped[int]
    diastolic: Mapped[int]
    pulse: Mapped[int | None]
    irregular_pulse_flag: Mapped[bool] = mapped_column(default=False)
    measured_at: Mapped[datetime]
    arm: Mapped[str | None] = mapped_column(String(8))         # left | right
    position: Mapped[str | None] = mapped_column(String(16))   # seated | standing | supine
    pre_medication: Mapped[bool | None]
    valid: Mapped[bool] = mapped_column(default=True)
    invalid_reason: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(32), default="manual")


class DeviceRhythmResult(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "device_rhythm_results"
    __table_args__ = (Index("ix_rhythm_user_time", "user_id", "recorded_at"),)

    user_id: Mapped[str] = user_fk(index=False)
    source: Mapped[str] = mapped_column(String(32))  # RHYTHM_SOURCES
    label: Mapped[str] = mapped_column(String(48))   # RHYTHM_LABELS
    raw_label: Mapped[str | None] = mapped_column(String(200))  # always keep the device's own string
    recorded_at: Mapped[datetime]
    file_url: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[JsonDict | None] = mapped_column(JSON)


class DailyLog(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "daily_logs"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_daily_log_user_date"),)

    user_id: Mapped[str] = user_fk()
    date: Mapped[date]
    sleep_quality: Mapped[int | None]  # 1-5
    fatigue: Mapped[int | None]        # 1-5
    mood: Mapped[int | None]           # 1-5
    stress: Mapped[int | None]         # 1-5
    # {"tremor","stiffness","fall","memory_lapse","confusion","headache","dizziness",
    #  "palpitations","chest_discomfort","breathlessness","vision_disturbance","speech_disturbance"}: bool
    flags: Mapped[dict[str, bool] | None] = mapped_column(JSON)
    medication_adherence: Mapped[bool | None]
    caffeine_alcohol: Mapped[JsonDict | None] = mapped_column(JSON)  # {"caffeine_cups":2,"alcohol_units":1}
    exercise_minutes: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(Text)


class Observation(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "observations"
    __table_args__ = (Index("ix_observations_user_time", "user_id", "created_at"),)

    user_id: Mapped[str] = user_fk(index=False)
    kind: Mapped[str] = mapped_column(String(32))   # OBSERVATION_KINDS
    label: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str | None] = mapped_column(String(32))
    onset: Mapped[str | None] = mapped_column(String(100))  # free-form ("3 days ago") or ISO date
    free_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="chat")  # OBSERVATION_SOURCES
    source_id: Mapped[str | None] = mapped_column(String(64))  # e.g. ChatMessage.id / DailyLog.id


class ScreenerResponse(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "screener_responses"
    __table_args__ = (Index("ix_screener_user_time", "user_id", "created_at"),)

    user_id: Mapped[str] = user_fk(index=False)
    screener: Mapped[str] = mapped_column(String(32))  # pd | cardio | ...
    answers: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    score: Mapped[float | None]


class AssessmentTask(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "assessment_tasks"
    __table_args__ = (Index("ix_assessment_tasks_user_time", "user_id", "created_at"),)

    user_id: Mapped[str] = user_fk(index=False)
    task_type: Mapped[str] = mapped_column(String(32))  # TASK_TYPES
    protocol_version: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(8))
    recording_quality: Mapped[JsonDict | None] = mapped_column(JSON)  # {"snr_db","duration_s","clipping"}
    raw_url: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[JsonDict | None] = mapped_column(JSON)
    features: Mapped[JsonDict | None] = mapped_column(JSON)
    model_scores: Mapped[JsonDict | None] = mapped_column(JSON)  # {name: {version, probability, threshold, ...}}
    score: Mapped[float | None]
    valid: Mapped[bool] = mapped_column(default=True)
    invalid_reason: Mapped[str | None] = mapped_column(String(200))


class UploadedDocument(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "uploaded_documents"

    user_id: Mapped[str] = user_fk()
    kind: Mapped[str] = mapped_column(String(32))  # doctor_note | lab | bp_photo | omron_pdf | apple_health | audio
    file_url: Mapped[str | None] = mapped_column(Text)
    extracted: Mapped[JsonDict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending | processing | done | failed


# ================================================================ Chat + RAG

class ChatConversation(IdMixin, CreatedAtMixin, Base):
    """One chat thread (the web app's "Chat history" list). Belongs to one user; its messages point
    at it with ``ChatMessage.conversation_id``. ``updated_at`` moves on every turn (newest first)."""

    __tablename__ = "chat_conversations"
    __table_args__ = (Index("ix_chat_conversations_user_updated", "user_id", "updated_at"),)

    user_id: Mapped[str] = user_fk(index=False)
    title: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class ChatMessage(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_user_time", "user_id", "created_at"),
        Index("ix_chat_messages_conversation_time", "conversation_id", "created_at"),
    )

    user_id: Mapped[str] = user_fk(index=False)
    # The thread it belongs to (NULL for messages from before conversations existed, and for the
    # iOS Shortcut / voice turns sent without one). No foreign key, so ``add_missing_columns`` can
    # add it to an existing table; ownership is checked through ``user_id`` instead.
    conversation_id: Mapped[str | None] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    audio_url: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSON)  # [{id,title,url,snippet}]


class ResearchSource(IdMixin, Base):
    __tablename__ = "research_sources"

    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None]
    url: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(200))
    pathway_tags: Mapped[list | None] = mapped_column(JSON)  # ["cardio", ...]
    kind: Mapped[str | None] = mapped_column(String(32))     # guideline | study | review | device_doc

    chunks: Mapped[list["ResearchChunk"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True, order_by="ResearchChunk.ordinal"
    )


class ResearchParent(IdMixin, Base):
    """A full paper section (what the LLM reads); child ResearchChunks are what get embedded."""

    __tablename__ = "research_parents"

    source_id: Mapped[str] = mapped_column(ForeignKey("research_sources.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(default=0)
    section: Mapped[str | None] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)


class ResearchChunk(IdMixin, Base):
    __tablename__ = "research_chunks"

    source_id: Mapped[str] = mapped_column(ForeignKey("research_sources.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("research_parents.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(VectorType(EMBEDDING_DIM))
    meta: Mapped[JsonDict | None] = mapped_column(JSON)

    source: Mapped[ResearchSource] = relationship(back_populates="chunks")


# ================================================================ Assessment / follow-up

class RiskAssessment(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (Index("ix_risk_user_pathway_time", "user_id", "pathway", "created_at"),)

    user_id: Mapped[str] = user_fk(index=False)
    pathway: Mapped[str] = mapped_column(String(16))  # PATHWAYS
    level: Mapped[str] = mapped_column(String(32))    # RISK_LEVELS
    priority_score: Mapped[int | None]                # 0-100 follow-up priority, null for insufficient/invalid
    fired_rules: Mapped[list | None] = mapped_column(JSON)
    missing_inputs: Mapped[list | None] = mapped_column(JSON)
    data_quality: Mapped[JsonDict | None] = mapped_column(JSON)
    model_versions: Mapped[JsonDict | None] = mapped_column(JSON)
    evidence: Mapped[list | None] = mapped_column(JSON)
    probabilities: Mapped[list | None] = mapped_column(JSON)
    preventive_measures: Mapped[list | None] = mapped_column(JSON)
    explanation: Mapped[str | None] = mapped_column(Text)

    reports: Mapped[list["Report"]] = relationship(back_populates="assessment", passive_deletes=True)


class Report(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "reports"

    assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("risk_assessments.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = user_fk()
    markdown: Mapped[str | None] = mapped_column(Text)
    html: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    # None until the user has an active 'share_with_clinic' consent; nulled on revoke (review fix 5)
    share_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)

    assessment: Mapped[RiskAssessment | None] = relationship(back_populates="reports")


class Appointment(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "appointments"

    user_id: Mapped[str] = user_fk()
    assessment_id: Mapped[str | None] = mapped_column(ForeignKey("risk_assessments.id", ondelete="SET NULL"))
    clinic: Mapped[JsonDict | None] = mapped_column(JSON)          # {name,address,phone,email,place_id}
    proposed_slots: Mapped[list | None] = mapped_column(JSON)      # [{"start","end"}] ISO strings
    chosen_slot: Mapped[JsonDict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="proposed")  # APPOINTMENT_STATUSES
    calendar_event_id: Mapped[str | None] = mapped_column(String(200))
    channel: Mapped[str | None] = mapped_column(String(16))        # email | phone | web
    agent_log: Mapped[list | None] = mapped_column(JSON)           # [{ts, tool, input, output}]
    # Booking v2: candidate clinics + per-channel attempts, approval, reply threading, external ids.
    candidates: Mapped[list | None] = mapped_column(JSON)          # ranked clinic dicts considered
    channel_attempts: Mapped[list | None] = mapped_column(JSON)    # [{channel, status, reason, ts}]
    approval: Mapped[JsonDict | None] = mapped_column(JSON)        # {requested_at, approved_at, shared_fields}
    reply_token: Mapped[str | None] = mapped_column(String(64), unique=True)  # book+<token>@domain
    external_ref: Mapped[str | None] = mapped_column(String(200))  # call/conversation id, web confirmation no.
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AppointmentMessage(IdMixin, CreatedAtMixin, Base):
    """One inbound/outbound exchange on a booking channel (email, call transcript, web submit)."""

    __tablename__ = "appointment_messages"

    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = user_fk()
    direction: Mapped[str] = mapped_column(String(8))     # out | in
    channel: Mapped[str] = mapped_column(String(16))      # email | phone | web
    sender: Mapped[str | None] = mapped_column(String(320))
    recipient: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text)
    classification: Mapped[JsonDict | None] = mapped_column(JSON)  # {outcome, slot, requested_info, ...}
    external_id: Mapped[str | None] = mapped_column(String(200), unique=True)  # message/call id (idempotency)


class Notification(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_time", "user_id", "created_at"),)

    user_id: Mapped[str] = user_fk(index=False)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[JsonDict | None] = mapped_column(JSON)
    read_at: Mapped[datetime | None]


class OAuthCredential(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "oauth_credentials"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),)

    user_id: Mapped[str] = user_fk()
    provider: Mapped[str] = mapped_column(String(32))  # google
    encrypted_tokens: Mapped[str] = mapped_column(Text)  # Fernet(json tokens) via core.security.encrypt_str
    scopes: Mapped[list | None] = mapped_column(JSON)
    expires_at: Mapped[datetime | None]


# ================================================================ Ops

class Job(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "jobs"

    name: Mapped[str] = mapped_column(String(100), index=True)
    scheduled_for: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | done | failed
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[JsonDict | None] = mapped_column(JSON)


class AuditLog(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_user_time", "user_id", "created_at"),)

    # Nullable + SET NULL would keep audit rows after account deletion; we cascade instead
    # so DELETE /api/me removes everything tied to the user.
    user_id: Mapped[str | None] = user_fk(nullable=True, index=False)
    actor: Mapped[str] = mapped_column(String(32), default="user")  # user | system | llm | admin | agent
    action: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str] = mapped_column(String(200))
    detail: Mapped[JsonDict | None] = mapped_column(JSON)


# ================================================================ Medications
# The medication list itself is ``Profile.medications`` (the risk engine, chat context, reports and
# document extraction already read it); see app/services/medication_service.py. These two tables
# hold what happens to those medications. ``medication_id`` is the id of an entry in that list.

REFILL_STATUSES = ("requested", "cancelled")
REFILL_METHODS = ("pickup", "delivery")


class RefillRequest(IdMixin, CreatedAtMixin, Base):
    """A refill the user asked the pharmacy for. At most one ``requested`` row per medication."""

    __tablename__ = "refill_requests"
    __table_args__ = (Index("ix_refill_requests_user_status", "user_id", "status"),)

    user_id: Mapped[str] = user_fk(index=False)
    medication_id: Mapped[str] = mapped_column(String(36))
    pharmacy_id: Mapped[str | None] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(16), default="pickup")  # pickup | delivery
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="requested")  # requested | cancelled
    cancelled_at: Mapped[datetime | None] = mapped_column()


class DoseLog(IdMixin, CreatedAtMixin, Base):
    """One scheduled dose ticked off on one day. ``dose_key`` = "<medication id>-<time>", e.g. "a1b2-8:00 AM"."""

    __tablename__ = "dose_logs"
    __table_args__ = (UniqueConstraint("user_id", "day", "dose_key", name="uq_dose_logs_user_day_key"),)

    user_id: Mapped[str] = user_fk(index=False)
    day: Mapped[date] = mapped_column()
    dose_key: Mapped[str] = mapped_column(String(120))


# ================================================================ Visits the user books themselves

BOOKING_STATUSES = ("requested", "cancelled")


class VisitBooking(IdMixin, CreatedAtMixin, Base):
    """A visit the user booked by picking a provider and a time in the web app (the booking agent's
    own appointments are ``Appointment``). ``provider`` is a snapshot of the clinic as shown then, so
    the booking still reads right if the directory changes. ``slot`` is a local ISO time
    ("2026-10-21T10:00") or NULL for "next available". The clinic is not contacted."""

    __tablename__ = "visit_bookings"
    __table_args__ = (Index("ix_visit_bookings_user_status", "user_id", "status"),)

    user_id: Mapped[str] = user_fk(index=False)
    provider_id: Mapped[str] = mapped_column(String(64))
    provider: Mapped[JsonDict] = mapped_column(JSON)  # {name, specialty, clinic, address, phone, glyph}
    slot: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="requested")  # requested | cancelled
    cancelled_at: Mapped[datetime | None] = mapped_column()


# ================================================================ Uploaded file contents

class StoredFile(IdMixin, CreatedAtMixin, Base):
    """File bytes kept in the database when STORAGE_BACKEND=database (see providers/storage.py), so
    uploads survive redeploys on hosts whose disk is temporary (Render free plan). Not in
    USER_OWNED_MODELS: the JSON export skips raw bytes, and DELETE /api/me removes them through
    ``Storage.delete_user_files`` (and the user FK cascade)."""

    __tablename__ = "stored_files"
    __table_args__ = (Index("ix_stored_files_user", "user_id"),)

    user_id: Mapped[str] = user_fk(index=False)
    kind: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(default=0)
    content: Mapped[bytes] = mapped_column(LargeBinary)


# Order matters for deletes (children before parents).
USER_OWNED_MODELS: tuple[type[Base], ...] = (
    AppointmentMessage, Report, Appointment, RiskAssessment, Notification, OAuthCredential, ChatMessage,
    ChatConversation, RefillRequest, DoseLog, VisitBooking,
    UploadedDocument, AssessmentTask, ScreenerResponse, Observation, DailyLog,
    DeviceRhythmResult, BpReading, MetricSample, DeviceToken, Consent, Profile, AuditLog,
)
