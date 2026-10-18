"""Booking agent v2 orchestrator (docs/specs/booking-v2.md).

    appt = run_booking_agent(db, user, assessment, llm, calendar, places, email)   # -> awaiting_approval
    appt = approve_appointment(db, user, appt.id)        # runs the channel plan -> requested | confirmed
    appt = decline_appointment(db, user, appt.id)        # user declines the plan -> cancelled
    appt = submit_user_info(db, user, appt.id, answer="…", accept=None)   # needs_user_info -> requested
    out  = apply_outcome(db, appt, {"outcome": "confirmed", "slot": "…"}, channel="email", message=msg)
    appt = confirm_appointment(db, appt.id, slot_index, token)   # public clinic confirm (POST)
    appt = cancel_appointment(db, user, appt.id)

Planning (no outbound contact): consent gate → Appointment(status=proposed) → LLM tool loop
(`find_clinics`, `check_channels`, `get_free_slots`, `propose_slots`, `notify_user`; works with any
LlmProvider: Gemini, Anthropic, Fake) → deterministic completion so FakeLlm / refusals never
stall: first ranked candidate with at least one usable channel, 3 spread slots → approval card
(`Appointment.approval` = {status, requested_at, clinic, channel_plan, slots, shared_fields, missing_profile_fields,
referral_note, report_url, user_message}) → status `awaiting_approval` + notification
`appointment_approval_needed`. With BOOKING_REQUIRE_APPROVAL=false the plan runs immediately.

Execution: channels in order web → email → phone. A channel whose `available()` returns a reason
(login wall, CAPTCHA, not configured, sandbox target missing, …) is skipped instantly and recorded
in `channel_attempts`; the first `requested` / `confirmed` result wins. When every channel of a
clinic fails, or the clinic declines, the next candidate clinic is planned; contacting a different
clinic needs a new approval (status back to `awaiting_approval`). No candidates left → `failed`.

Outcomes (inbound e-mail, phone post-call webhook) all go through `apply_outcome`:
confirmed → `confirmed` + calendar event confirmed, only for an approved slot (± SLOT_TOLERANCE;
any other time is a counter-offer); counter_offer → auto-accepted only when it arrived by e-mail,
is free in the user's calendar and within opening hours AND the acceptance reply was sent, else
`needs_user_info` (phone: the user is asked to call back); needs_info → `needs_user_info` +
`appointment_needs_info` notification; declined → next candidate.

Concurrency: approve / decline / submit_user_info / confirm_appointment claim the expected status
atomically (`_claim`: UPDATE … WHERE status = expected → PROCESSING, committed before any side
effect) — a concurrent second call raises AppointmentStateError (HTTP 409).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import audit, has_consent
from app.db.models import Appointment, AppointmentMessage, Report, RiskAssessment, User
from app.integrations import Clinic, Slot
from app.prompts import booking as booking_prompt
from app.providers.llm import ToolSpec
from app.services import notify as notify_svc
from app.services import user_emails
from app.services.booking import sandbox
from app.services.booking.channels import CHANNEL_ORDER, BookingContext, ChannelMessage, ChannelResult, default_channels
from app.services.booking.common import (
    KIND_APPOINTMENT_APPROVAL_NEEDED, KIND_APPOINTMENT_NEEDS_INFO, LABELS, append_log, entry, json_safe,
    missing_profile_fields, patient_info, reason_for, report_url_for, slot_label, tz_of, utcnow, verify_confirm_token,
)
from app.services.booking.discovery import REFERRAL_NOTE, discover_clinics
from app.services.booking.slots import DEFAULT_TZ, clinic_timezone, plan_slots, slot_is_bookable

log = get_logger(__name__)

MAX_TURNS = 12
#: one approval covers this many ranked clinics (chosen + backups), tried in order without asking again
APPROVAL_CANDIDATES = 3
OPEN_STATUSES = ("awaiting_approval", "requested", "needs_user_info")
ACTIVE_STATUSES = ("proposed", "awaiting_approval", "processing", "requested", "needs_user_info", "confirmed")
#: Health-card / OHIP-like numbers are never forwarded to a clinic (spec §0). Flagged: the context
#: words (health card, OHIP, HCN, health number), the Ontario 4-3-3 grouping (+ optional 2-letter
#: version code), 10 digits + version code, and 10 plain digits that cannot be a NANP phone number
#: (starting with 0/1). Plain phone numbers (416-555-0199, 4165550199, +1 …) are not flagged.
HEALTH_CARD_RE = re.compile(
    r"health[\s-]*card|\bohip\b|\bhcn\b|health[\s-]*(?:insurance[\s-]*)?number"
    r"|\b\d{4}[\s-]\d{3}[\s-]\d{3}(?:[\s-]?[A-Za-z]{2})?\b"
    r"|\b\d{10}[\s-]?[A-Za-z]{2}\b"
    r"|(?<![\d+])\b[01]\d{9}\b", re.I)
#: a clinic's "confirmed" time must be within this of an approved slot, else it is a counter-offer
SLOT_TOLERANCE = timedelta(minutes=5)
#: transient status while an approval / answer / confirm is being executed (atomic claim)
PROCESSING = "processing"


class AppointmentStateError(ValueError):
    """The appointment cannot change in its current status (e.g. approve after cancel)."""


# ================================================================ environment helpers

@dataclass
class Env:
    db: Session
    user: User
    calendar: Any
    email: Any
    llm: Any
    channels: dict[str, Any]
    detector: Any
    use_freebusy: bool
    events_allowed: bool
    tz: str
    now: datetime | None = None


def _env(db: Session, user: User, *, calendar: Any = None, email: Any = None, llm: Any = None,
         channels: dict[str, Any] | None = None, detector: Any = None, now: datetime | None = None) -> Env:
    from app.integrations.google_calendar import get_calendar_client
    from app.providers.email import get_email_provider

    calendar = calendar if calendar is not None else get_calendar_client(db, user.id)
    cal_ok = has_consent(db, user.id, "calendar_access") and bool(getattr(calendar, "connected", False))
    return Env(db=db, user=user, calendar=calendar, email=email if email is not None else get_email_provider(),
               llm=llm, channels=channels or default_channels(), detector=detector, use_freebusy=cal_ok,
               events_allowed=cal_ok, tz=tz_of(user), now=now)


def _pathway(db: Session, appt: Appointment) -> str:
    a = db.get(RiskAssessment, appt.assessment_id) if appt.assessment_id else None
    return a.pathway if a else "general"


def _clinic_obj(d: dict[str, Any] | Clinic) -> Clinic:
    return d if isinstance(d, Clinic) else Clinic.from_dict(d)


def _clinic_tz(clinic: Clinic | dict[str, Any], fallback: str) -> str:
    return clinic_timezone(clinic, fallback)


def _needs_detection(clinic: Clinic, channels: dict[str, Any]) -> bool:
    web = channels.get("web")
    web_on = bool(web is not None and getattr(web, "configured", lambda: False)())
    return bool(clinic.website) and (web_on or not clinic.email)


def detect_for(clinic: Clinic, env: Env) -> dict[str, Any] | None:
    """Run website detection when it matters (web channel configured, or no e-mail known);
    copies a found e-mail / form URL into clinic.extra. Returns DetectionResult.as_dict().

    Detection always reads the clinic's REAL website (GET only, robots.txt respected, ≤5 pages,
    10 s) — also in sandbox mode, so the plan reflects reality. Only execution (form submit,
    e-mail, call) is redirected to the sandbox targets (sandbox.resolve_target)."""
    if not _needs_detection(clinic, env.channels):
        return None
    from app.services.booking.channels.detect import detect_channels

    detector = env.detector or detect_channels
    url = clinic.website
    try:
        result = detector(url)
        det = result.as_dict() if hasattr(result, "as_dict") else dict(result)
    except Exception as exc:  # detection never blocks the flow
        det = {"website": url, "web": "none", "reason": f"detection failed: {type(exc).__name__}", "emails": []}
    if det.get("emails") and not clinic.email:
        clinic.extra["detected_email"] = det["emails"][0]
    if det.get("form_url"):
        clinic.extra["form_url"] = det["form_url"]
    det["fetched"] = "real_website_read_only"
    return det


def _ctx(env: Env, appt: Appointment, clinic: Clinic, slots: list[Slot], pathway: str, report_url: str,
         detection: dict[str, Any] | None, **kw: Any) -> BookingContext:
    ctz = _clinic_tz(clinic, env.tz)
    return BookingContext(
        db=env.db, user=env.user, appointment=appt, clinic=clinic, slots=slots, clinic_tz=ctz, user_tz=env.tz,
        patient=patient_info(env.user, pathway, slots, ctz, report_url), report_url=report_url, llm=env.llm,
        calendar=env.calendar, email=env.email, now=env.now, detection=detection, use_freebusy=env.use_freebusy,
        calendar_events_allowed=env.events_allowed, pathway=pathway, **kw)


def channel_plan(env: Env, ctx: BookingContext) -> list[dict[str, Any]]:
    """[{channel, available, reason, target, real, redirected}] in CHANNEL_ORDER."""
    plan = []
    for name in CHANNEL_ORDER:
        ch = env.channels.get(name)
        reason = "not configured" if ch is None else ch.available(ctx.clinic, ctx)
        target = sandbox.resolve_target(name, ctx.clinic) if reason is None else None
        plan.append({"channel": name, "available": reason is None, "reason": reason,
                     "target": target.value if target else None,
                     "real": sandbox.real_detail(name, ctx.clinic),
                     "redirected": bool(target and target.redirected)})
    return plan


def _clinic_summary(c: Clinic) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "specialty": c.specialty, "kind": c.kind, "address": c.address,
            "distance_km": round(c.distance_km, 2) if c.distance_km is not None else None, "rating": c.rating,
            "phone": c.phone, "email": c.email or (c.extra or {}).get("detected_email"), "website": c.website,
            "source": c.source, "fictional": c.fictional}


# ================================================================ planning session (LLM tools)

@dataclass
class PlanningSession:
    env: Env
    appointment: Appointment
    assessment: RiskAssessment
    report_url: str
    places: Any = None
    geocoder: Any = None
    candidates: dict[str, Clinic] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    detections: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    plans: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    slot_plans: dict[str, Any] = field(default_factory=dict)
    clinic: Clinic | None = None
    proposed: list[Slot] = field(default_factory=list)
    preference: str | None = None
    user_message: str | None = None
    discovery_log: dict[str, Any] | None = None
    log: list[dict[str, Any]] = field(default_factory=list)
    exclude: set[str] = field(default_factory=set)
    backups: list[tuple[Clinic, list[Slot]]] = field(default_factory=list)

    @property
    def pathway(self) -> str:
        return self.assessment.pathway

    def _clinic(self, clinic_id: Any) -> Clinic:
        if not self.candidates:
            self.find_clinics({})
        clinic = self.candidates.get(str(clinic_id))
        if clinic is None:
            raise ValueError(f"unknown clinic_id {clinic_id!r}: use an id returned by find_clinics")
        return clinic

    # ---- tools
    def find_clinics(self, inp: dict[str, Any]) -> dict[str, Any]:
        if not self.candidates:
            res = discover_clinics(self.env.user.profile, self.pathway, places=self.places, geocoder=self.geocoder)
            self.discovery_log = res.as_log()
            for c in res.candidates:
                if c.id in self.exclude:
                    continue
                self.candidates[c.id] = c
                self.order.append(c.id)
        return {"clinics": [{"clinic_id": c.id, "name": c.name, "specialty": c.specialty, "kind": c.kind,
                             "distance_km": round(c.distance_km, 2) if c.distance_km is not None else None,
                             "rating": c.rating, "has_opening_hours": bool(c.opening_hours),
                             "has_email": bool(c.email), "has_phone": bool(c.phone), "has_website": bool(c.website)}
                            for c in (self.candidates[i] for i in self.order)],
                "note": REFERRAL_NOTE}

    def check_channels(self, inp: dict[str, Any]) -> dict[str, Any]:
        clinic = self._clinic(inp.get("clinic_id"))
        if clinic.id not in self.plans:
            det = detect_for(clinic, self.env)
            self.detections[clinic.id] = det
            if det is not None:
                self.log.append(entry("detection", clinic_id=clinic.id, url=det.get("website"), web=det.get("web"),
                                      reason=det.get("reason"), form_url=det.get("form_url"),
                                      emails=len(det.get("emails") or []), pages=len(det.get("pages") or []),
                                      robots_disallowed=len(det.get("robots_disallowed") or []),
                                      note="read-only fetch of the real website; execution follows "
                                           f"BOOKING_TARGET_MODE={settings.booking_target_mode}"))
            ctx = _ctx(self.env, self.appointment, clinic, [], self.pathway, self.report_url, det)
            self.plans[clinic.id] = channel_plan(self.env, ctx)
        plan = self.plans[clinic.id]
        return {"clinic_id": clinic.id, "usable": [p["channel"] for p in plan if p["available"]],
                "skipped": {p["channel"]: p["reason"] for p in plan if not p["available"]}}

    def get_free_slots(self, inp: dict[str, Any]) -> dict[str, Any]:
        clinic = self._clinic(inp.get("clinic_id"))
        if clinic.id not in self.slot_plans:
            self.slot_plans[clinic.id] = plan_slots(clinic, calendar=self.env.calendar,
                                                    use_freebusy=self.env.use_freebusy, now=self.env.now,
                                                    fallback_tz=self.env.tz)
        sp = self.slot_plans[clinic.id]
        return {"clinic_id": clinic.id, "source": sp.source, "clinic_timezone": sp.clinic_tz, "notes": sp.notes,
                "suggested": [s.start.isoformat() for s in sp.proposals],
                "slots": [s.as_dict() | {"label": slot_label(s, sp.clinic_tz)} for s in sp.available[:24]]}

    def propose_slots(self, inp: dict[str, Any]) -> dict[str, Any]:
        clinic = self._clinic(inp.get("clinic_id"))
        raw = inp.get("slots")
        if not isinstance(raw, list) or len(raw) != 3:
            raise ValueError("exactly 3 slots are required")
        if clinic.id not in self.slot_plans:
            self.get_free_slots({"clinic_id": clinic.id})
        by_start = {s.start: s for s in self.slot_plans[clinic.id].available}
        out = []
        for r in raw:
            s = Slot.from_any(r)
            match = by_start.get(s.start)
            if match is None:
                raise ValueError(f"slot {s.start.isoformat()} is not one of the free slots from get_free_slots")
            out.append(match)
        if len({s.start for s in out}) != 3:
            raise ValueError("the 3 slots must be different")
        if clinic.id not in self.plans:
            self.check_channels({"clinic_id": clinic.id})
        if not any(p["available"] for p in self.plans[clinic.id]):
            raise ValueError("this clinic has no usable channel (see check_channels); pick another clinic")
        pref = str(inp.get("preference") or "").lower() or None
        self.clinic, self.proposed, self.preference = clinic, sorted(out, key=lambda s: s.start), pref
        return {"clinic_id": clinic.id, "clinic": clinic.name,
                "proposed": [s.as_dict() | {"label": slot_label(s, _clinic_tz(clinic, self.env.tz))} for s in out]}

    def notify_user(self, inp: dict[str, Any]) -> dict[str, Any]:
        message = " ".join(str(inp.get("message") or "").split())[:500]
        if not message:
            raise ValueError("message is required")
        self.user_message = message
        return {"queued": True, "note": "shown on the approval card"}

    def tools(self) -> list[ToolSpec]:
        cid = {"type": "object", "properties": {"clinic_id": {"type": "string"}}, "required": ["clinic_id"]}
        return [
            ToolSpec("find_clinics", "Ranked clinics near the patient for this pathway (best first).",
                     {"type": "object", "properties": {}}, self.find_clinics),
            ToolSpec("check_channels", "Which contact channels (web form, e-mail, phone) can be used for a clinic.",
                     cid, self.check_channels),
            ToolSpec("get_free_slots", "Free 30-minute slots: patient calendar ∩ clinic opening hours, next 14 days.",
                     cid, self.get_free_slots),
            ToolSpec("propose_slots", "Record the chosen clinic and exactly 3 slots (ISO starts from get_free_slots).",
                     {"type": "object", "properties": {
                         "clinic_id": {"type": "string"},
                         "slots": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
                         "preference": {"type": "string", "enum": ["morning", "afternoon", "any"]}},
                      "required": ["clinic_id", "slots"]}, self.propose_slots),
            ToolSpec("notify_user", "One or two plain sentences for the patient's approval card.",
                     {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
                     self.notify_user),
        ]

    # ---- deterministic completion
    def det(self, name: str, inp: dict[str, Any]) -> Any:
        handler = {t.name: t.handler for t in self.tools()}[name]
        try:
            out = handler(inp)
            self.log.append(entry("tool", tool=name, input=inp, output=out, is_error=False, mode="deterministic"))
            return out
        except Exception as exc:
            self.log.append(entry("tool", tool=name, input=inp, output=f"{type(exc).__name__}: {exc}",
                                  is_error=True, mode="deterministic"))
            raise

    def complete(self) -> None:
        """Finish whatever the LLM did not: first ranked candidate with a usable channel + 3 slots."""
        if self.clinic is not None and len(self.proposed) == 3:
            return
        if not self.candidates:
            self.det("find_clinics", {})
        for cid in self.order:
            plan = self.check_channels({"clinic_id": cid})
            if not plan["usable"]:
                self.log.append(entry("note", text=f"{self.candidates[cid].name}: no usable channel",
                                      skipped=plan["skipped"]))
                continue
            slots = self.det("get_free_slots", {"clinic_id": cid})
            if len(slots["suggested"]) < 3:
                self.log.append(entry("note", text=f"{self.candidates[cid].name}: fewer than 3 free slots"))
                continue
            self.det("propose_slots", {"clinic_id": cid, "slots": slots["suggested"]})
            return
        raise LookupError("no candidate clinic with a usable channel and 3 free slots")

    def collect_backups(self, n: int = APPROVAL_CANDIDATES - 1) -> None:
        """The next `n` usable candidates after the chosen clinic (covered by the same approval)."""
        self.backups = []
        for cid in self.order:
            if len(self.backups) >= n:
                return
            if self.clinic is not None and cid == self.clinic.id:
                continue
            if not self.check_channels({"clinic_id": cid})["usable"]:
                continue
            sp = self.get_free_slots({"clinic_id": cid})
            if len(sp["suggested"]) < 3:
                continue
            by = {x.start.isoformat(): x for x in self.slot_plans[cid].proposals}
            self.backups.append((self.candidates[cid], [by[x] for x in sp["suggested"]]))


# ================================================================ approval card

def _slot_rows(env: Env, clinic: Clinic | dict[str, Any], slots: list[Slot]) -> list[dict[str, Any]]:
    ctz = _clinic_tz(clinic, env.tz)
    return [sl.as_dict() | {"label": slot_label(sl, ctz), "label_user": slot_label(sl, env.tz)} for sl in slots]


def _candidate_entry(env: Env, s: PlanningSession, rank: int, clinic: Clinic, slots: list[Slot]) -> dict[str, Any]:
    return {"rank": rank, "clinic_id": clinic.id, "summary": _clinic_summary(clinic), "clinic": clinic.as_dict(),
            "channel_plan": s.plans[clinic.id], "slots": _slot_rows(env, clinic, slots),
            "detection": s.detections.get(clinic.id),
            "referral_note": REFERRAL_NOTE if clinic.kind == "specialist" else None}


#: Per-request parts of `shared_fields` (not from the profile), kept when the profile part is rebuilt.
_REQUEST_FIELDS = ("reason_category", "preferred_slots", "report_url")


def refresh_pending_approvals(db: Session, user: User) -> int:
    """Rebuild the profile part of every awaiting-approval card's `shared_fields` (and
    `missing_profile_fields`) after a profile edit. Approving builds the clinic's details from the
    profile as it is then (`_ctx`), so the card shows what will actually be sent. Not committed."""
    profile_part = patient_info(user, "general", [], DEFAULT_TZ, "").shared_fields()
    n = 0
    for appt in db.scalars(select(Appointment).where(Appointment.user_id == user.id,
                                                      Appointment.status == "awaiting_approval")):
        approval = dict(appt.approval or {})
        old = approval.get("shared_fields") or {}
        approval["shared_fields"] = json_safe(
            {k: v for k, v in profile_part.items() if k not in _REQUEST_FIELDS}
            | {k: old[k] for k in _REQUEST_FIELDS if k in old})
        approval["missing_profile_fields"] = missing_profile_fields(user)
        appt.approval = approval
        n += 1
    return n


def _approval(env: Env, s: PlanningSession, clinic: Clinic, slots: list[Slot], plan: list[dict[str, Any]],
              detection: dict[str, Any] | None, user_message: str | None) -> dict[str, Any]:
    """The approval card: the chosen clinic (top-level fields, kept for compatibility) plus
    `candidates` = the up to APPROVAL_CANDIDATES ranked clinics this ONE approval covers, each with
    its channel plan and slots. `shared_fields` are the same for every candidate (only the
    proposed times differ per clinic)."""
    ctz = _clinic_tz(clinic, env.tz)
    pi = patient_info(env.user, s.pathway, slots, ctz, s.report_url)
    shared = pi.shared_fields()
    shared.pop("preferred_slots", None)
    entries = [_candidate_entry(env, s, 1, clinic, slots)] + [
        _candidate_entry(env, s, i + 2, c, sl) for i, (c, sl) in enumerate(s.backups)]
    return json_safe({
        "status": "pending", "requested_at": utcnow().isoformat(), "clinic": _clinic_summary(clinic),
        "channel_plan": plan, "slots": _slot_rows(env, clinic, slots),
        "shared_fields": shared | {"preferred_slots": pi.preferred_slots},
        # onboarding gaps: what the agent cannot answer if the clinic asks (frontend: "complete profile")
        "missing_profile_fields": missing_profile_fields(env.user),
        "referral_note": REFERRAL_NOTE if clinic.kind == "specialist" else None,
        "report_url": s.report_url, "slot_preference": s.preference,
        # LLM free text never speaks in the app's voice: kept only as a clearly labelled note
        "agent_note": {"label": "AI-generated note (not verified)", "text": user_message} if user_message else None,
        "detection": detection, "target_mode": settings.booking_target_mode,
        "candidates": entries, "candidate_ids": [e["clinic_id"] for e in entries], "active_rank": 1,
    })


def _plan_summary(plan: list[dict[str, Any]]) -> str:
    usable = [p["channel"] for p in plan if p["available"]]
    return " → ".join(usable) if usable else "none"


def _await_approval(env: Env, appt: Appointment, s: PlanningSession) -> Appointment:
    clinic = s.clinic
    plan = s.plans[clinic.id]
    s.collect_backups()
    appt.clinic = json_safe(clinic.as_dict())
    appt.proposed_slots = [x.as_dict() for x in s.proposed]
    appt.approval = _approval(env, s, clinic, s.proposed, plan, s.detections.get(clinic.id), s.user_message)
    appt.approval = appt.approval | {"finding": _finding(s.assessment)}
    appt.status = "awaiting_approval"
    append_log(appt, entry("approval_requested", clinic=clinic.name, channel_plan=_plan_summary(plan),
                           candidates=[c.name for c, _ in [(clinic, None)] + s.backups]))
    dist = f" ({clinic.distance_km:.1f} km away)" if clinic.distance_km is not None and clinic.distance_km < 500 else ""
    backup = (f" If it cannot book you, we will try {' and then '.join(c.name for c, _ in s.backups)}."
              if s.backups else "")
    # structured data only (clinic name / plan from the discovery data, never the planner's free text)
    body = (f"We found {clinic.name}{dist}. We will contact it by {_plan_summary(plan)} and propose 3 times."
            + backup + " Nothing is sent until you approve.")
    notify_svc.notify(env.db, env.user.id, KIND_APPOINTMENT_APPROVAL_NEEDED, f"Approve appointment request: {clinic.name}",
                      body, payload={"appointment_id": appt.id, "clinic": _clinic_summary(clinic),
                                     "channel_plan": plan, "slots": appt.approval["slots"],
                                     "shared_fields": appt.approval["shared_fields"],
                                     "missing_profile_fields": appt.approval["missing_profile_fields"],
                                     "finding": appt.approval["finding"],
                                     "candidates": [{"rank": e["rank"], "clinic": e["summary"],
                                                     "channel_plan": e["channel_plan"], "slots": e["slots"]}
                                                    for e in appt.approval["candidates"]]},
                      email=True, email_content=_email_or_none(user_emails.approval_email, env.user, appt), commit=False)
    return appt


def _finding(assessment: RiskAssessment | None) -> dict[str, Any]:
    """Why the agent ran, in the user's words: pathway name, the neutral headline and up to three
    rule summaries (structured data from the risk engine; the LLM never writes this)."""
    if assessment is None:
        return {}
    from app.services.risk_service import PATHWAY_NAMES

    headline = ((assessment.data_quality or {}).get("headline") or {})
    points = [f.get("summary_text") for f in (assessment.fired_rules or [])
              if f.get("summary_text") and f.get("level") in ("followup_recommended", None)]
    return json_safe({"pathway": assessment.pathway,
                      "pathway_name": PATHWAY_NAMES.get(assessment.pathway, {}).get("en", assessment.pathway),
                      "headline": headline.get("en") if isinstance(headline, dict) else None,
                      "points": points[:3], "priority_score": assessment.priority_score,
                      "assessment_id": assessment.id})


def _email_or_none(builder: Any, *args: Any) -> Any:
    """A broken e-mail template must never block a booking step: fall back to the plain rendering."""
    try:
        return builder(*args)
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("user e-mail rendering failed", extra={"builder": getattr(builder, "__name__", "?"),
                                                              "error": str(exc)})
        return None


# ================================================================ entry point

def _report_for(db: Session, user: User, assessment: RiskAssessment, llm: Any, language: str) -> Report:
    from app.services.report_service import ensure_share_token, generate_report, latest_report_for

    report = latest_report_for(db, assessment.id) or generate_report(db, user, assessment, llm, language, commit=False)
    return ensure_share_token(db, report)


def run_booking_agent(db: Session, user: User, assessment: RiskAssessment, llm: Any, calendar: Any = None,
                      places: Any = None, email: Any = None, language: str | None = None, *,
                      report: Report | None = None, commit: bool = True, channels: dict[str, Any] | None = None,
                      detector: Any = None, geocoder: Any = None, now: datetime | None = None) -> Appointment | None:
    """Plan an appointment request for `assessment` (module docstring). Returns the Appointment
    (`awaiting_approval`; `requested`/`confirmed` when approval is off; `failed` when no clinic
    can be used) or None without `share_with_clinic` consent (nothing stored, nothing sent)."""
    from app.services import llm_audit_hook

    if assessment.user_id != user.id:
        raise PermissionError("assessment does not belong to user")
    if assessment.level == "emergency":
        raise ValueError("the booking agent never runs for emergency assessments")
    if not has_consent(db, user.id, "share_with_clinic"):
        audit(db, user.id, "booking.skipped", f"risk_assessment:{assessment.id}",
              {"reason": "no_consent:share_with_clinic"}, actor="agent", commit=commit)
        return None

    env = _env(db, user, calendar=calendar, email=email, llm=llm, channels=channels, detector=detector, now=now)
    if report is not None:
        from app.services.report_service import ensure_share_token

        ensure_share_token(db, report)
    report = report or _report_for(db, user, assessment, llm, "en")
    appt = Appointment(user_id=user.id, assessment_id=assessment.id, status="proposed", agent_log=[], channel_attempts=[])
    db.add(appt)
    db.flush()
    s = PlanningSession(env=env, appointment=appt, assessment=assessment, report_url=report_url_for(report),
                        places=places, geocoder=geocoder)
    s.log.append(entry("note", text="booking agent started", pathway=assessment.pathway, report_id=report.id,
                       target_mode=settings.booking_target_mode))
    if not env.use_freebusy:
        s.log.append(entry("note", text="no calendar access: slots from clinic opening hours only; "
                                        "no calendar event will be created"))
    transcript: list[dict[str, Any]] = []
    if llm is not None:
        try:
            _, transcript = llm.run_tools(
                booking_prompt.system_prompt(assessment.pathway, reason_for(assessment.pathway)),
                [{"role": "user", "content": booking_prompt.user_message(assessment.pathway)}],
                s.tools(), max_turns=MAX_TURNS, purpose="booking_agent",
                audit_hook=llm_audit_hook(db, user.id, f"appointment:{appt.id}"))
        except Exception as exc:
            s.log.append(entry("note", text=f"LLM tool loop failed: {type(exc).__name__}: {exc}"[:500]))
    agent_entries = [json_safe(e | {"mode": "llm"}) for e in transcript]
    failure = None
    if s.clinic is None or len(s.proposed) != 3:
        s.log.append(entry("note", text="LLM did not finish the plan; running the deterministic steps"))
    try:
        s.complete()
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
    appt.candidates = [json_safe(s.candidates[i].as_dict()) for i in s.order]
    appt.agent_log = [s.log[0]] + agent_entries + ([entry("discovery", **s.discovery_log)] if s.discovery_log else []) \
        + s.log[1:]
    if failure:
        _fail(env, appt, failure)
    else:
        _await_approval(env, appt, s)
        if not settings.booking_require_approval:
            append_log(appt, entry("note", text="BOOKING_REQUIRE_APPROVAL=false: running the plan immediately"))
            _approve(env, appt, by="auto")
    audit(db, user.id, "booking.run", f"appointment:{appt.id}",
          {"status": appt.status, "clinic": (appt.clinic or {}).get("name"),
           "llm_tool_calls": sum(1 for e in transcript if e.get("type") == "tool"),
           "deterministic_fallback": any(e.get("mode") == "deterministic" for e in s.log)}, actor="agent")
    db.commit() if commit else db.flush()
    return appt


# ================================================================ execution

def _record_attempt(appt: Appointment, clinic_id: str, channel: str, status: str, reason: str | None,
                    external_ref: str | None = None, target: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    att = json_safe({"ts": utcnow().isoformat(), "clinic_id": clinic_id, "channel": channel, "status": status,
                     "reason": reason, "external_ref": external_ref,
                     "target": (target or {}).get("value"), "real": (target or {}).get("real"),
                     "redirected": (target or {}).get("redirected", False), **kw})
    appt.channel_attempts = list(appt.channel_attempts or []) + [att]
    return att


def store_messages(db: Session, appt: Appointment, messages: list[ChannelMessage]) -> list[AppointmentMessage]:
    """Persist ChannelMessages (idempotent on external_id)."""
    rows = []
    for m in messages:
        if m.external_id and db.scalar(select(AppointmentMessage.id).where(AppointmentMessage.external_id == m.external_id)):
            continue
        row = AppointmentMessage(appointment_id=appt.id, user_id=appt.user_id, direction=m.direction, channel=m.channel,
                                 sender=m.sender, recipient=m.recipient, subject=(m.subject or "")[:500] or None,
                                 body=m.body, classification=m.classification, external_id=m.external_id)
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def _ctx_for(env: Env, appt: Appointment, **kw: Any) -> BookingContext:
    approval = appt.approval or {}
    clinic = _clinic_obj(appt.clinic or {})
    slots = [Slot.from_any(x) for x in appt.proposed_slots or []]
    kw.setdefault("providers", {"slot_preference": approval.get("slot_preference")})
    return _ctx(env, appt, clinic, slots, _pathway(env.db, appt), approval.get("report_url") or "",
                approval.get("detection"), **kw)


def _tentative_event(env: Env, appt: Appointment, ctx: BookingContext) -> None:
    if settings.calendar_write_requires_approval:
        return  # the user adds the confirmed time themselves (add_to_calendar); no silent hold
    if appt.calendar_event_id or not env.events_allowed or not ctx.slots:
        return
    try:
        others = ", ".join(slot_label(s, env.tz) for s in ctx.slots)
        appt.calendar_event_id = env.calendar.create_event(
            f"Tentative: appointment request — {ctx.clinic.name}", ctx.slots[0].start, ctx.slots[0].end,
            f"Requested via health assistant. Proposed: {others}. Report: {ctx.report_url}", tentative=True)
        append_log(appt, entry("calendar_event", event_id=appt.calendar_event_id, status="tentative"))
    except Exception as exc:
        append_log(appt, entry("note", text=f"calendar event failed: {exc}"[:300]))


def _on_requested(env: Env, appt: Appointment, ctx: BookingContext, channel: str, result: ChannelResult) -> None:
    appt.status = "requested"
    appt.channel = channel
    appt.external_ref = result.external_ref or appt.external_ref
    clinic = dict(appt.clinic or {})
    req = json_safe(result.details.get("request") or {
        "channel": channel, "to": (result.target or {}).get("value"), "real_to": (result.target or {}).get("real"),
        "redirected": (result.target or {}).get("redirected", False), "sent_at": utcnow().isoformat()})
    clinic["requests"] = dict(clinic.get("requests") or {}) | {channel: req}
    if channel == "email" or not clinic.get("request"):
        clinic["request"] = req  # the e-mail thread (cancel notice, replies) stays reachable
    appt.clinic = clinic
    _tentative_event(env, appt, ctx)
    via = {"web": "the clinic's online form", "email": "e-mail", "phone": "phone"}.get(channel, channel)
    notify_svc.notify(env.db, appt.user_id, notify_svc.KIND_APPOINTMENT_REQUESTED,
                      f"Appointment request sent to {ctx.clinic.name}",
                      f"We asked {ctx.clinic.name} for an appointment by {via} and proposed 3 times. "
                      "We will let you know when the clinic replies.",
                      payload={"appointment_id": appt.id, "clinic": ctx.clinic.name, "channel": channel,
                               "proposed_slots": appt.proposed_slots or [], "report_url": ctx.report_url},
                      email=True, email_content=_email_or_none(user_emails.requested_email, env.user, appt, channel),
                      commit=False)


def execute_plan(env: Env, appt: Appointment, only: list[str] | None = None, reminder: bool = False) -> ChannelResult | None:
    """Try channels in CHANNEL_ORDER (or `only`) for the current clinic until one succeeds.
    Returns the winning result, or None when every channel was skipped / failed."""
    ctx = _ctx_for(env, appt, reminder=reminder)
    approved = _approved_channels(appt)
    for name in only or CHANNEL_ORDER:
        if reminder and name == "web":
            continue  # a web form is never re-submitted as a reminder
        ch = env.channels.get(name)
        reason = "not configured" if ch is None else ch.available(ctx.clinic, ctx)
        if reason is None and approved is not None and name not in approved:
            reason = "not on the approved channel plan"  # only the channels the user approved for this clinic
        if reason is not None:
            _record_attempt(appt, ctx.clinic.id, name, "skipped", reason)
            append_log(appt, entry("channel", channel=name, status="skipped", reason=reason))
            continue
        try:
            result = ch.execute(appt, ctx)
        except Exception as exc:
            result = ChannelResult.failed(f"{type(exc).__name__}: {exc}"[:300])
        store_messages(env.db, appt, result.messages)
        _record_attempt(appt, ctx.clinic.id, name, result.status, result.reason, result.external_ref, result.target,
                        reminder=reminder or None)
        append_log(appt, entry("channel", channel=name, status=result.status, reason=result.reason,
                               external_ref=result.external_ref, details=result.details))
        audit(env.db, appt.user_id, f"booking.{name}.{result.status}", f"appointment:{appt.id}",
              {"clinic_id": ctx.clinic.id, "target": (result.target or {}).get("value"),
               "redirected": (result.target or {}).get("redirected")}, actor="agent")
        if result.status == "confirmed":
            appt.channel, appt.external_ref = name, result.external_ref or appt.external_ref
            finalize_confirmed(env, appt, result.slot or (appt.proposed_slots or [None])[0], via=name)
            return result
        if result.status == "requested":
            if not reminder:
                _on_requested(env, appt, ctx, name, result)
            return result
    return None


def _approved_channels(appt: Appointment) -> set[str] | None:
    """Channels the approval card listed as usable for the active clinic (None = no card)."""
    plan = (appt.approval or {}).get("channel_plan")
    if not isinstance(plan, list):
        return None
    return {p.get("channel") for p in plan if isinstance(p, dict) and p.get("available")}


def _fail(env: Env, appt: Appointment, reason: str) -> None:
    appt.status = "failed"
    append_log(appt, entry("failed", reason=reason))
    name = (appt.clinic or {}).get("name", "the clinic")
    notify_svc.notify(env.db, appt.user_id, notify_svc.KIND_APPOINTMENT_FAILED, f"Could not book: {name}",
                      f"We could not request an appointment ({reason}). You may want to call a clinic directly.",
                      payload={"appointment_id": appt.id, "reason": reason}, email=True, commit=False)


def next_candidate(env: Env, appt: Appointment, reason: str) -> Appointment:
    """Move to the next untried candidate clinic: plan it and ask for approval again (contacting
    another clinic shares data with a new recipient). None left -> failed."""
    tried = {a.get("clinic_id") for a in appt.channel_attempts or []} | {(appt.clinic or {}).get("id")}
    append_log(appt, entry("next_candidate", reason=reason, tried=sorted(t for t in tried if t)))
    approval = appt.approval or {}
    if approval.get("status") == "approved":
        backup = next((e for e in approval.get("candidates") or [] if e.get("clinic_id") not in tried), None)
        if backup is not None:
            return _switch_to_approved(env, appt, backup, reason)
    tried |= set(approval.get("candidate_ids") or [])  # covered by the earlier card: never re-ask
    assessment = env.db.get(RiskAssessment, appt.assessment_id) if appt.assessment_id else None
    if assessment is None:
        _fail(env, appt, reason)
        return appt
    s = PlanningSession(env=env, appointment=appt, assessment=assessment,
                        report_url=(appt.approval or {}).get("report_url") or "")
    for c in appt.candidates or []:
        clinic = _clinic_obj(c)
        if clinic.id in tried:
            continue
        s.candidates[clinic.id] = clinic
        s.order.append(clinic.id)
    if not s.order:
        _fail(env, appt, f"{reason}; no other clinic to try")
        return appt
    try:
        s.complete()
    except LookupError as exc:
        appt.agent_log = list(appt.agent_log or []) + s.log
        _fail(env, appt, f"{reason}; {exc}")
        return appt
    appt.agent_log = list(appt.agent_log or []) + s.log
    appt.calendar_event_id = _drop_event(env, appt)
    appt.reply_token, appt.external_ref, appt.channel = None, None, None
    _await_approval(env, appt, s)
    if not settings.booking_require_approval:
        _approve(env, appt, by="auto")
    return appt


def _approved_slots_valid(env: Env, rows: list[dict[str, Any]]) -> list[Slot] | None:
    """The slots shown on the card for this clinic, or None when any is now < 24 h away (they can
    only be used as approved; new times need a new approval)."""
    try:
        slots = [Slot.from_any(r) for r in rows]
    except (KeyError, TypeError, ValueError):
        return None
    now = env.now or utcnow()
    if len(slots) == 3 and all(x.start >= now + timedelta(hours=24) for x in slots):
        return slots
    return None


def _set_active(env: Env, appt: Appointment, cand: dict[str, Any], clinic: Clinic, slots: list[Slot]) -> dict[str, Any]:
    appt.calendar_event_id = _drop_event(env, appt)
    appt.reply_token, appt.external_ref, appt.channel = None, None, None
    appt.clinic = json_safe(clinic.as_dict())
    appt.proposed_slots = [x.as_dict() for x in slots]
    approval = dict(appt.approval or {})
    approval.update(clinic=cand["summary"], channel_plan=cand["channel_plan"], slots=_slot_rows(env, clinic, slots),
                    detection=cand.get("detection"), referral_note=cand.get("referral_note"),
                    active_rank=cand.get("rank"))
    shared = dict(approval.get("shared_fields") or {})
    shared["preferred_slots"] = [slot_label(x, _clinic_tz(clinic, env.tz)) for x in slots]
    approval["shared_fields"] = shared
    return approval


def _reapprove(env: Env, appt: Appointment, cand: dict[str, Any], clinic: Clinic, reason: str) -> Appointment:
    """The approved times for a backup clinic are no longer valid: propose new ones and ask again."""
    slots = plan_slots(clinic, calendar=env.calendar, use_freebusy=env.use_freebusy, now=env.now,
                       fallback_tz=env.tz).proposals
    if len(slots) < 3:
        _fail(env, appt, f"{reason}; {clinic.name} has fewer than 3 free slots")
        return appt
    approval = _set_active(env, appt, cand, clinic, slots)
    approval.update(status="pending", requested_at=utcnow().isoformat(), reapproval_reason="approved times expired")
    approval["candidates"] = [(dict(c) | {"slots": approval["slots"]}) if c.get("clinic_id") == clinic.id else c
                              for c in approval.get("candidates") or []]
    appt.approval = json_safe(approval)
    appt.status = "awaiting_approval"
    append_log(appt, entry("approval_requested", clinic=clinic.name, channel_plan=_plan_summary(cand["channel_plan"]),
                           reason=f"{reason}; the approved times for {clinic.name} are no longer available"))
    notify_svc.notify(env.db, appt.user_id, KIND_APPOINTMENT_APPROVAL_NEEDED, f"Approve new times: {clinic.name}",
                      f"The times you approved for {clinic.name} are no longer available. Please approve 3 new "
                      "times. Nothing is sent until you approve.",
                      payload={"appointment_id": appt.id, "clinic": cand.get("summary"),
                               "channel_plan": cand.get("channel_plan"), "slots": approval["slots"]},
                      email=True, commit=False)
    return appt


def _switch_to_approved(env: Env, appt: Appointment, cand: dict[str, Any], reason: str) -> Appointment:
    """Fall through to the next clinic of the approved card — no new approval needed, but only with
    the times and channels shown on that card."""
    clinic = _clinic_obj(cand["clinic"])
    slots = _approved_slots_valid(env, cand.get("slots") or [])
    if slots is None:
        return _reapprove(env, appt, cand, clinic, reason)
    appt.approval = json_safe(_set_active(env, appt, cand, clinic, slots))
    append_log(appt, entry("next_candidate", clinic=clinic.name, rank=cand.get("rank"), auto_approved=True,
                           reason=f"{reason}; covered by the approval"))
    result = execute_plan(env, appt)
    if result is None:
        return next_candidate(env, appt, f"no channel reached {clinic.name}")
    return appt


def _drop_event(env: Env, appt: Appointment) -> None:
    if appt.calendar_event_id:
        try:
            env.calendar.delete_event(appt.calendar_event_id)
        except Exception:
            pass
    return None


def _approve(env: Env, appt: Appointment, by: str = "user", channel: str | None = None) -> Appointment:
    """Run the approved plan: every usable channel in order, or only `channel` when the user
    picked one. If it doesn't reach the clinic, the next candidate is planned as usual."""
    approval = dict(appt.approval or {})
    approval.update(status="approved", approved_at=utcnow().isoformat(), approved_by=by)
    if channel:
        approval["chosen_channel"] = channel
    appt.approval = approval
    append_log(appt, entry("approved", by=by, channel=channel))
    result = execute_plan(env, appt, only=[channel] if channel else None)
    if result is None:
        next_candidate(env, appt, "no channel reached the clinic")
    return appt


# ================================================================ user actions

def _own(db: Session, user: User, appointment_id: str) -> Appointment:
    appt = db.get(Appointment, appointment_id)
    if appt is None or appt.user_id != user.id:
        raise LookupError("appointment not found")
    return appt


def _claim(db: Session, appt: Appointment, expected: str) -> None:
    """Atomically move `expected` -> PROCESSING before any side effect (UPDATE … WHERE id AND
    status = expected; rowcount checked; works on SQLite and Postgres) and commit, so a concurrent
    second request sees PROCESSING and gets AppointmentStateError (HTTP 409)."""
    res = db.execute(update(Appointment).where(Appointment.id == appt.id, Appointment.status == expected)
                     .values(status=PROCESSING).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        db.refresh(appt)
        raise AppointmentStateError(f"appointment is {appt.status}, not {expected} (already being processed?)")
    db.commit()
    appt.status = PROCESSING


def _release(db: Session, appt: Appointment, expected: str) -> None:
    """After an unexpected error: undo the partial work and put the status back."""
    db.rollback()
    db.refresh(appt)
    if appt.status == PROCESSING:
        appt.status = expected
        db.commit()


def _settle(appt: Appointment, expected: str) -> None:
    """Safety net: a code path that did not set a final status returns to `expected`."""
    if appt.status == PROCESSING:
        append_log(appt, entry("note", text=f"no status change; back to {expected}"))
        appt.status = expected


def approve_appointment(db: Session, user: User, appointment_id: str, *, calendar: Any = None, email: Any = None,
                        llm: Any = None, channels: dict[str, Any] | None = None, now: datetime | None = None,
                        by: str = "user", channel: str | None = None) -> Appointment:
    """The user approves the plan on the card -> channels run (spec §1.5). 409 unless awaiting_approval
    (also for a second concurrent approve: the status is claimed atomically first). ``by`` records
    where the approval came from ("user" = the app, "user:email_link" = the e-mail button).
    ``channel`` (web | email | phone): contact the clinic only that way; it must be one the card
    lists as available (ValueError otherwise)."""
    appt = _own(db, user, appointment_id)
    if appt.status != "awaiting_approval":
        raise AppointmentStateError(f"appointment is {appt.status}, not awaiting_approval")
    if not has_consent(db, user.id, "share_with_clinic"):
        raise AppointmentStateError("share_with_clinic consent is not active")
    if channel is not None and channel not in (_approved_channels(appt) or set()):
        raise ValueError(f"{channel} is not an available way to reach this clinic")
    if llm is None:
        from app.providers.llm import get_llm_provider

        llm = get_llm_provider()
    env = _env(db, user, calendar=calendar, email=email, llm=llm, channels=channels, now=now)
    _claim(db, appt, "awaiting_approval")
    try:
        _approve(env, appt, by=by, channel=channel)
        _settle(appt, "awaiting_approval")
        audit(db, user.id, "appointment.approve", f"appointment:{appt.id}",
              {"status": appt.status, "by": by, "channel": channel})
        db.commit()
    except Exception:
        _release(db, appt, "awaiting_approval")
        raise
    return appt


def decline_appointment(db: Session, user: User, appointment_id: str, *, by: str = "user") -> Appointment:
    appt = _own(db, user, appointment_id)
    if appt.status != "awaiting_approval":
        raise AppointmentStateError(f"appointment is {appt.status}, not awaiting_approval")
    _claim(db, appt, "awaiting_approval")
    approval = dict(appt.approval or {})
    approval.update(status="declined", declined_at=utcnow().isoformat(), declined_by=by)
    appt.approval = approval
    appt.status = "cancelled"
    append_log(appt, entry("cancelled", by="patient", reason="approval declined", via=by))
    audit(db, user.id, "appointment.decline", f"appointment:{appt.id}", {"by": by})
    db.commit()
    return appt


def submit_user_info(db: Session, user: User, appointment_id: str, answer: str | None = None,
                     accept: bool | None = None, *, calendar: Any = None, email: Any = None,
                     now: datetime | None = None) -> Appointment:
    """Answer the clinic's question / counter-offer (status needs_user_info). Raises
    AppointmentStateError (wrong status, concurrent answer), ValueError (missing answer,
    health-card number). A counter-offer is only `confirmed` once the acceptance actually reached
    the clinic (e-mail thread) — or, without any reply path (phone), when the user says they booked
    it with the clinic themselves."""
    appt = _own(db, user, appointment_id)
    if appt.status != "needs_user_info":
        raise AppointmentStateError(f"appointment is {appt.status}, not needs_user_info")
    q = dict((appt.approval or {}).get("pending_question") or {})
    answer = " ".join((answer or "").split())[:1000] or None
    if answer and HEALTH_CARD_RE.search(answer):
        raise ValueError("health-card numbers are never shared by the assistant; please give them to the clinic "
                         "directly at your visit")
    if q.get("type") == "counter_offer" and accept is None:
        raise ValueError("accept (true/false) is required for a counter-offer")
    if q.get("type") != "counter_offer" and not answer:
        raise ValueError("answer is required")
    env = _env(db, user, calendar=calendar, email=email, now=now)
    _claim(db, appt, "needs_user_info")
    try:
        _answer(env, appt, q, answer, accept)
        _settle(appt, "needs_user_info")
        audit(db, user.id, "appointment.user_info", f"appointment:{appt.id}", {"type": q.get("type"), "accept": accept})
        db.commit()
    except Exception:
        _release(db, appt, "needs_user_info")
        raise
    log.info("user info submitted", extra={"appointment_id": appt.id, "clinic": (appt.clinic or {}).get("name")})
    return appt


def _answer(env: Env, appt: Appointment, q: dict[str, Any], answer: str | None, accept: bool | None) -> None:
    tz = _clinic_tz(appt.clinic or {}, env.tz)
    if q.get("type") != "counter_offer":
        sent = _reply(env, appt, f"Thank you for your question. The patient's answer: {answer}")
        _clear_question(appt, answer=answer, delivered=sent)
        appt.status = "confirmed" if q.get("after_confirmation") else "requested"
        if not sent:
            _tell_user_no_reply(env, appt, "Your answer could not be sent to the clinic automatically. Please "
                                           "contact the clinic directly with your answer.")
        return
    slot = Slot.from_any(q["slot"])
    if not accept:
        labels = "; ".join(slot_label(Slot.from_any(x), tz) for x in appt.proposed_slots or [])
        sent = _reply(env, appt, "Thank you — unfortunately that time does not work for the patient. "
                      f"Could you offer another time? The original options were: {labels}."
                      + (f"\n{answer}" if answer else ""))
        _clear_question(appt, answer=answer, accept=False, delivered=sent)
        # declining a new time for a confirmed appointment keeps the original booking
        appt.status = "confirmed" if q.get("after_confirmation") else "requested"
        return
    if _reply_path(appt):
        sent = _reply(env, appt, f"Thank you — the patient accepts {slot_label(slot, tz)}."
                      + (f"\n{answer}" if answer else "") + "\nPlease confirm the booking.")
        if not sent:  # the clinic was NOT told: keep asking instead of pretending it is booked
            _ask_user(env, appt, q | {"question": f"We could not send your acceptance of {slot_label(slot, env.tz)} "
                                                  f"to {(appt.clinic or {}).get('name', 'the clinic')}. Please try "
                                                  "again or call the clinic.", "send_failed": True})
            return
        _clear_question(appt, answer=answer, accept=True, delivered=True)
        finalize_confirmed(env, appt, slot.as_dict(), via="counter_offer_user_accepted")
        return
    # no reply path (phone): the user was asked to call the clinic back; accept=true means they did
    _clear_question(appt, answer=answer, accept=True, delivered=False, booked_by="patient")
    append_log(appt, entry("note", text="counter-offer accepted by the patient directly with the clinic "
                                        "(no reply path for the assistant)"))
    finalize_confirmed(env, appt, slot.as_dict(), via="counter_offer_booked_by_patient")


def _tell_user_no_reply(env: Env, appt: Appointment, body: str) -> None:
    name = (appt.clinic or {}).get("name", "the clinic")
    notify_svc.notify(env.db, appt.user_id, KIND_APPOINTMENT_NEEDS_INFO, f"{name}: please contact the clinic",
                      body, payload={"appointment_id": appt.id, "phone": (appt.clinic or {}).get("phone")},
                      email=True, commit=False)


def _clear_question(appt: Appointment, **kw: Any) -> None:
    approval = dict(appt.approval or {})
    q = approval.pop("pending_question", None)
    approval["answered_questions"] = list(approval.get("answered_questions") or []) + [
        json_safe((q or {}) | kw | {"answered_at": utcnow().isoformat()})]
    appt.approval = approval
    append_log(appt, entry("user_info", **kw))


def _email_thread(appt: Appointment) -> dict[str, Any] | None:
    """The e-mail request sent to the current clinic (whatever channel was used last)."""
    clinic = appt.clinic or {}
    for req in (((clinic.get("requests") or {}).get("email")), clinic.get("request")):
        if isinstance(req, dict) and "@" in str(req.get("to") or "") and req.get("accepted", True) is not False:
            return req
    return None


def _reply_path(appt: Appointment) -> str | None:
    """How the assistant can answer the clinic: 'email' when this appointment has an e-mail request
    thread with the current clinic, else None (phone / web have no reply thread)."""
    return "email" if _email_thread(appt) else None


def _reply(env: Env, appt: Appointment, body: str, channel: str | None = None) -> bool:
    """Reply to the clinic; True only when the reply was actually sent (provider accepted). Uses the
    e-mail thread whenever there is one (also after a follow-up switched `appt.channel` to phone);
    `channel` is the channel the clinic's message arrived on (logged)."""
    thread = _email_thread(appt)
    if thread is not None:
        from app.services.booking.channels.email import send_followup

        ok, msg, err = send_followup(appt, env.email, env.user, body, thread=thread)
        if msg is not None:
            store_messages(env.db, appt, [msg])
        append_log(appt, entry("reply", channel="email", arrived_on=channel or appt.channel, accepted=ok, error=err))
        return ok
    append_log(appt, entry("reply", channel=channel or appt.channel, accepted=False,
                           note="no reply thread; recorded only", body=body[:300]))
    return False


# ================================================================ outcomes

def _ask_user(env: Env, appt: Appointment, question: dict[str, Any]) -> None:
    if appt.status == "confirmed":  # clinic follow-up after confirmation: remember what to restore
        question = question | {"after_confirmation": True, "previous_slot": appt.chosen_slot}
    approval = dict(appt.approval or {})
    approval["pending_question"] = json_safe(question | {"asked_at": utcnow().isoformat()})
    appt.approval = approval
    appt.status = "needs_user_info"
    append_log(appt, entry("needs_user_info", question=question))
    name = (appt.clinic or {}).get("name", "The clinic")
    notify_svc.notify(env.db, appt.user_id, KIND_APPOINTMENT_NEEDS_INFO, f"{name} needs your answer",
                      question.get("question") or "The clinic asked a question about your appointment request.",
                      payload={"appointment_id": appt.id, "question": approval["pending_question"]},
                      email=True, commit=False)


def _match_proposed(slot: Slot, proposed: list[dict[str, Any]] | None) -> Slot | None:
    """The approved slot within SLOT_TOLERANCE of `slot`, else None."""
    for raw in proposed or []:
        try:
            p = Slot.from_any(raw)
        except (KeyError, TypeError, ValueError):
            continue
        if abs(p.start - slot.start) <= SLOT_TOLERANCE:
            return p
    return None


def apply_outcome(db: Session, appt: Appointment, outcome: dict[str, Any], *, channel: str,
                  message: ChannelMessage | None = None, calendar: Any = None, email: Any = None,
                  now: datetime | None = None) -> dict[str, Any]:
    """Handle a clinic's answer (inbound e-mail, phone post-call, …). `outcome` =
    {outcome: confirmed|counter_offer|needs_info|declined|other, slot: ISO|None, requested_info, summary}.
    Stores `message` (with the classification). Returns {"action": …}. Caller commits.

    Text from the clinic (e-mail body, call transcript) is untrusted: a "confirmed" only finalizes
    when its time is one of the approved slots (± SLOT_TOLERANCE); any other time is a counter-offer.
    A counter-offer is auto-accepted only when it arrived by e-mail, is bookable (free + opening
    hours) AND the acceptance reply was actually sent; otherwise the user is asked (phone: the
    assistant never accepts by itself — the user is asked to call the clinic back)."""
    user = db.get(User, appt.user_id)
    env = _env(db, user, calendar=calendar, email=email, now=now)
    kind = outcome.get("outcome") or "other"
    if message is not None:
        message.classification = json_safe(outcome)
        store_messages(db, appt, [message])
    append_log(appt, entry("clinic_reply", channel=channel, outcome=kind, summary=(outcome.get("summary") or "")[:300],
                           slot=outcome.get("slot")))
    post_confirm = appt.status == "confirmed"
    if post_confirm and kind not in ("needs_info", "counter_offer"):
        # After confirmation the clinic's text never changes state by itself (it is untrusted): a
        # re-confirmation is ignored; a cancellation or anything else is passed on to the user.
        if kind == "confirmed":
            append_log(appt, entry("note", text="clinic re-confirmed an already confirmed appointment"))
            return {"action": "ignored", "reason": "already confirmed"}
        name = (appt.clinic or {}).get("name", "The clinic")
        summary = (outcome.get("summary") or "The clinic sent a message about your confirmed appointment.")[:500]
        append_log(appt, entry("note", text=f"message after confirmation ({kind}) passed to the user"))
        notify_svc.notify(env.db, appt.user_id, KIND_APPOINTMENT_NEEDS_INFO,
                          f"Message from {name} about your confirmed appointment", summary,
                          payload={"appointment_id": appt.id, "outcome": kind, "after_confirmation": True},
                          email=True, commit=False)
        return {"action": "notified_user", "reason": f"{kind} after confirmation"}
    if not post_confirm and appt.status not in ("requested", "needs_user_info"):
        append_log(appt, entry("note", text=f"reply ignored: appointment is {appt.status}"))
        return {"action": "ignored", "reason": f"appointment is {appt.status}"}
    clinic = appt.clinic or {}
    tz = _clinic_tz(clinic, env.tz)
    slot = _parse_slot(outcome.get("slot"), tz)
    name = clinic.get("name", "The clinic")

    if kind == "confirmed" and slot is not None:
        approved = _match_proposed(slot, appt.proposed_slots)
        if approved is not None:
            if appt.status == "needs_user_info":
                _clear_question(appt, superseded_by=f"clinic_confirmed_{channel}")
            finalize_confirmed(env, appt, approved.as_dict(), via=channel)
            return {"action": "confirmed", "slot": approved.as_dict()}
        append_log(appt, entry("note", text=f"clinic 'confirmed' {slot.start.isoformat()}, which is not one of the "
                                            "approved times: handled as a counter-offer"))
        kind = "counter_offer"
    if kind == "counter_offer" and slot is not None:
        ok, why = slot_is_bookable(slot, clinic, calendar=env.calendar, use_freebusy=env.use_freebusy,
                                   now=now or utcnow(), fallback_tz=env.tz)
        if post_confirm:
            ok, why = False, "the appointment was already confirmed; a new time needs your OK"
        if ok and channel == "email" and _reply_path(appt) == "email":
            if _reply(env, appt, f"Thank you — {slot_label(slot, tz)} works for the patient. Please consider it booked.",
                      channel=channel):
                finalize_confirmed(env, appt, slot.as_dict(), via="counter_offer_auto_accepted")
                return {"action": "counter_offer_accepted", "slot": slot.as_dict(), "reason": why}
            why = "the acceptance e-mail could not be sent"
        elif ok:
            why = f"offered by {channel}; the assistant cannot reply on that channel"
        when = slot_label(slot, env.tz)
        if _reply_path(appt):
            question = f"{name} offered {when} instead ({why}). Do you accept this time?"
        else:
            phone_no = clinic.get("phone") or "their number"
            question = (f"{name} offered {when} by {channel} ({why}). The assistant cannot call back to accept: "
                        f"please call {name} at {phone_no} to accept or decline, then tell us whether you booked "
                        "it (accept = yes / no).")
        _ask_user(env, appt, {"type": "counter_offer", "slot": slot.as_dict(), "reason": why, "channel": channel,
                              "reply_path": _reply_path(appt), "question": question})
        return {"action": "asked_user", "reason": why}
    if kind in ("needs_info", "counter_offer"):
        question = outcome.get("requested_info") or outcome.get("summary") or "The clinic asked a question."
        _ask_user(env, appt, {"type": "question", "question": str(question)[:500], "channel": channel,
                              "reply_path": _reply_path(appt)})
        return {"action": "asked_user"}
    if kind == "declined":
        _record_attempt(appt, clinic.get("id"), channel, "declined", (outcome.get("summary") or "declined")[:300])
        appt.calendar_event_id = _drop_event(env, appt)
        next_candidate(env, appt, f"{clinic.get('name', 'clinic')} declined")
        return {"action": "next_candidate", "status": appt.status}
    return {"action": "recorded"}


def _parse_slot(value: Any, tz: str) -> Slot | None:
    if not value:
        return None
    try:
        if isinstance(value, str):
            start = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if start.tzinfo is None:
                from zoneinfo import ZoneInfo

                start = start.replace(tzinfo=ZoneInfo(tz))
            return Slot(start, start + timedelta(minutes=30))
        return Slot.from_any(value)
    except (ValueError, TypeError, KeyError):
        return None


def finalize_confirmed(env: Env, appt: Appointment, slot: dict[str, Any] | None, via: str) -> Appointment:
    """status confirmed, chosen_slot, user notified (in-app + e-mail with the .ics attached).

    Calendar: with ``CALENDAR_WRITE_REQUIRES_APPROVAL`` (default) nothing is written — the card gets
    ``approval.calendar = {status: pending}`` and the notification / e-mail ask the user to tap
    "Add to calendar" (``add_to_calendar``). Otherwise the tentative event is confirmed / created here."""
    appt.status = "confirmed"
    appt.chosen_slot = slot
    entries = [entry("confirmed", slot=slot, via=via)]
    clinic_name = (appt.clinic or {}).get("name", "—")
    approval = dict(appt.approval or {})
    if slot and settings.calendar_write_requires_approval:
        approval["calendar"] = {"status": "pending", "requested_at": utcnow().isoformat(), "mode": _calendar_mode(env)}
        entries.append(entry("calendar_approval_requested", mode=approval["calendar"]["mode"]))
    elif slot:
        s = Slot.from_any(slot)
        summary = f"Appointment: {clinic_name}"
        try:
            if appt.calendar_event_id:
                ok = env.calendar.update_event(appt.calendar_event_id, status="confirmed", start=s.start, end=s.end,
                                               summary=summary)
                entries.append(entry("calendar_update", event_id=appt.calendar_event_id, ok=bool(ok)))
            elif env.events_allowed:
                appt.calendar_event_id = env.calendar.create_event(summary, s.start, s.end,
                                                                   "Booked via health assistant.", tentative=False)
                entries.append(entry("calendar_event", event_id=appt.calendar_event_id, status="confirmed"))
            if appt.calendar_event_id:
                approval["calendar"] = {"status": "added", "event_id": appt.calendar_event_id, "mode": "google",
                                        "at": utcnow().isoformat(), "by": "auto"}
        except Exception as exc:
            entries.append(entry("calendar_update", event_id=appt.calendar_event_id, ok=False, error=str(exc)[:300]))
    appt.approval = json_safe(approval)
    append_log(appt, *entries)
    when = slot_label(Slot.from_any(slot), env.tz) if slot else "a time to be confirmed"
    cal = calendar_sync_status(env, appt)
    body = f"{clinic_name} confirmed {when}."
    if cal.get("status") == "pending":
        body += " Add it to your calendar? Nothing is added until you say so."
    notify_svc.notify(env.db, appt.user_id, notify_svc.KIND_APPOINTMENT_CONFIRMED, f"Appointment confirmed: {clinic_name}",
                      body, payload={"appointment_id": appt.id, "chosen_slot": slot, "slot_label": when,
                                     "clinic": _clinic_summary(_clinic_obj(appt.clinic or {})) if appt.clinic else None,
                                     "calendar": cal},
                      email=True, email_content=_email_or_none(user_emails.confirmed_email, env.user, appt, cal),
                      commit=False)
    return appt


# ================================================================ calendar (user-approved writes)

def _calendar_mode(env: Env) -> str:
    """'google' = we can write the event through the API (connected + calendar_access consent);
    'manual' = the user adds it from the .ics / Google template link."""
    return "google" if env.events_allowed else "manual"


def calendar_sync_status(env: Env, appt: Appointment) -> dict[str, Any]:
    """`{status: pending|added|skipped|none, mode: google|manual, event_id?, add_url?, google_url?, ics_url?}`
    — what the app / e-mail shows under "Add to calendar"."""
    state = dict((appt.approval or {}).get("calendar") or {})
    if not appt.chosen_slot or appt.status != "confirmed":
        return {"status": "none", "mode": _calendar_mode(env)}
    status = state.get("status") or ("added" if appt.calendar_event_id else "pending")
    out = {"status": status, "mode": _calendar_mode(env), "event_id": appt.calendar_event_id or state.get("event_id"),
           "at": state.get("at")}
    if status != "added":
        out |= user_emails.calendar_options(appt, google_available=env.events_allowed)
        out["mode"] = _calendar_mode(env)
    return out


def add_to_calendar(db: Session, user: User, appointment_id: str, *, calendar: Any = None, by: str = "user") -> Appointment:
    """The user approved the calendar write: create (or confirm) the Google Calendar event.
    409 (`AppointmentStateError`) unless the appointment is confirmed with a time and Google Calendar is
    connected with the calendar_access consent; the caller then offers the .ics instead."""
    appt = _own(db, user, appointment_id)
    if appt.status != "confirmed" or not appt.chosen_slot:
        raise AppointmentStateError(f"appointment is {appt.status}; only a confirmed time can be added")
    env = _env(db, user, calendar=calendar)
    if not env.events_allowed:
        raise AppointmentStateError("Google Calendar is not connected (or the calendar_access consent is missing); "
                                    "use the .ics invitation instead")
    s = Slot.from_any(appt.chosen_slot)
    clinic_name = (appt.clinic or {}).get("name", "clinic")
    summary = f"Appointment: {clinic_name}"
    description = f"Booked via {settings.product_name}. " + ", ".join(appt.clinic.get(k) for k in ("address", "phone")
                                                                       if (appt.clinic or {}).get(k))
    state = dict((appt.approval or {}).get("calendar") or {})
    if appt.calendar_event_id:
        ok = env.calendar.update_event(appt.calendar_event_id, status="confirmed", start=s.start, end=s.end,
                                       summary=summary, description=description)
        if not ok:
            appt.calendar_event_id = env.calendar.create_event(summary, s.start, s.end, description, tentative=False)
    else:
        appt.calendar_event_id = env.calendar.create_event(summary, s.start, s.end, description, tentative=False)
    state.update(status="added", event_id=appt.calendar_event_id, mode="google", at=utcnow().isoformat(), by=by)
    appt.approval = json_safe(dict(appt.approval or {}) | {"calendar": state})
    append_log(appt, entry("calendar_event", event_id=appt.calendar_event_id, status="confirmed", by=by))
    audit(db, user.id, "appointment.calendar_add", f"appointment:{appt.id}", {"event_id": appt.calendar_event_id, "by": by})
    db.commit()
    return appt


def skip_calendar(db: Session, user: User, appointment_id: str) -> Appointment:
    """"Not now" on the add-to-calendar prompt: remembered so the app stops asking (the .ics stays
    available from the appointment menu)."""
    appt = _own(db, user, appointment_id)
    state = dict((appt.approval or {}).get("calendar") or {})
    if state.get("status") == "added":
        return appt
    state.update(status="skipped", at=utcnow().isoformat())
    appt.approval = json_safe(dict(appt.approval or {}) | {"calendar": state})
    append_log(appt, entry("calendar_skipped"))
    db.commit()
    return appt


def calendar_status_for(db: Session, user: User, appointment_id: str, *, calendar: Any = None) -> dict[str, Any]:
    appt = _own(db, user, appointment_id)
    return calendar_sync_status(_env(db, user, calendar=calendar), appt)


# ================================================================ e-mail buttons (signed links)

def _by_link(db: Session, appointment_id: str, action: str, token: str | None) -> tuple[Appointment, User]:
    from app.services.booking.common import approval_nonce, calendar_nonce, verify_user_action_token

    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise LookupError("appointment not found")
    nonce = calendar_nonce(appt) if action == "calendar" else approval_nonce(appt)
    if not verify_user_action_token(appt.id, action, nonce, token):
        raise PermissionError("invalid or expired link")
    user = db.get(User, appt.user_id)
    if user is None:
        raise LookupError("appointment not found")
    return appt, user


def link_preview(db: Session, appointment_id: str, action: str, token: str | None, *, calendar: Any = None) -> dict[str, Any]:
    """What the GET page of an e-mail button shows (never changes state): the appointment summary and
    whether the action can still run (`state`: ready | done | closed)."""
    appt, user = _by_link(db, appointment_id, action, token)
    env = _env(db, user, calendar=calendar)
    clinic = appt.clinic or {}
    tz = env.tz
    info: dict[str, Any] = {"appointment_id": appt.id, "action": action, "status": appt.status,
                            "clinic": clinic.get("name") or "the clinic", "address": clinic.get("address"),
                            "app_url": f"{settings.frontend_origin.rstrip('/')}/appointments?appointment={appt.id}",
                            "product": settings.product_name}
    if action in ("approve", "decline"):
        approval = appt.approval or {}
        info["slots"] = [x.get("label_user") or x.get("label") for x in approval.get("slots") or []]
        info["plan"] = user_emails._plan_sentence(approval.get("channel_plan") or [])
        shared = approval.get("shared_fields") or {}
        info["shared"] = [(label, str(shared[k])) for k, label in user_emails.SHARED_LABELS if shared.get(k)]
        info["state"] = "ready" if appt.status == "awaiting_approval" else "closed"
        info["state_text"] = {"cancelled": "This request was declined or cancelled.",
                              "requested": "This request was already approved and sent to the clinic.",
                              "confirmed": "This appointment is already confirmed.",
                              "needs_user_info": "This request was approved; the clinic has a question for you.",
                              "failed": "This request could not be completed.",
                              PROCESSING: "This request is being processed right now."}.get(appt.status, "")
    else:
        cal = calendar_sync_status(env, appt)
        info |= {"calendar": cal, "when": slot_label(Slot.from_any(appt.chosen_slot), tz) if appt.chosen_slot else None}
        info["state"] = ("done" if cal.get("status") == "added" else
                         "ready" if appt.status == "confirmed" and appt.chosen_slot else "closed")
        info["state_text"] = ("This appointment is already in your Google Calendar." if info["state"] == "done" else
                              "" if info["state"] == "ready" else "This appointment is no longer confirmed.")
    return info


def act_by_link(db: Session, appointment_id: str, action: str, token: str | None, *, calendar: Any = None,
                email: Any = None, llm: Any = None, channels: dict[str, Any] | None = None) -> Appointment:
    """The POST of an e-mail button: approve / decline the card, or add the confirmed time to Google Calendar."""
    appt, user = _by_link(db, appointment_id, action, token)
    if action == "approve":
        return approve_appointment(db, user, appt.id, calendar=calendar, email=email, llm=llm, channels=channels,
                                   by="user:email_link")
    if action == "decline":
        return decline_appointment(db, user, appt.id, by="user:email_link")
    return add_to_calendar(db, user, appt.id, calendar=calendar, by="user:email_link")


def ics_by_link(db: Session, appointment_id: str, token: str | None) -> tuple[bytes, str]:
    """(.ics bytes, filename) for the signed download link of the confirmation e-mail."""
    appt, user = _by_link(db, appointment_id, "calendar", token)
    if appt.status != "confirmed" or not appt.chosen_slot:
        raise AppointmentStateError("this appointment has no confirmed time")
    return user_emails.appointment_ics(appt, tz=tz_of(user)), f"appointment-{appt.id[:8]}.ics"


# ================================================================ clinic link / cancel / queries

def _confirm_target(db: Session, appointment_id: str, slot_index: int, token: str | None) -> tuple[Appointment, dict]:
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise LookupError("appointment not found")
    if not verify_confirm_token(appt.id, token, clinic_id=(appt.clinic or {}).get("id"), slot_index=slot_index):
        raise PermissionError("invalid confirmation token")
    slots = appt.proposed_slots or []
    if not 0 <= slot_index < len(slots):
        raise ValueError("invalid slot index")
    return appt, slots[slot_index]


def confirm_preview(db: Session, appointment_id: str, slot_index: int, token: str | None) -> dict[str, Any]:
    """What the GET confirm page shows (never changes state): {state: confirmable | already_confirmed
    | not_confirmable, label, clinic, message}. Raises like confirm_appointment."""
    appt, chosen = _confirm_target(db, appointment_id, slot_index, token)
    clinic = appt.clinic or {}
    label = slot_label(Slot.from_any(chosen), _clinic_tz(clinic, DEFAULT_TZ))
    if appt.status == "confirmed":
        state = "already_confirmed" if appt.chosen_slot == chosen else "not_confirmable"
        message = "This appointment is already confirmed for another time."
    elif appt.status in ("requested", "needs_user_info"):
        state, message = "confirmable", ""
    else:
        state, message = "not_confirmable", f"This appointment request is {appt.status}."
    return {"state": state, "label": label, "clinic": clinic.get("name") or "clinic", "message": message}


def confirm_appointment(db: Session, appointment_id: str, slot_index: int, token: str | None,
                        calendar: Any = None) -> Appointment:
    """Clinic confirm (POST from the confirm page; HMAC token bound to appointment + current clinic
    + slot index). Raises LookupError, PermissionError, ValueError (bad slot), AppointmentStateError
    (not requested / needs_user_info, concurrent confirm; same slot again is idempotent)."""
    appt, chosen = _confirm_target(db, appointment_id, slot_index, token)
    if appt.status == "confirmed":
        if appt.chosen_slot == chosen:
            return appt
        raise AppointmentStateError("appointment is already confirmed for another time")
    if appt.status not in ("requested", "needs_user_info"):
        raise AppointmentStateError(f"appointment is {appt.status}")
    previous = appt.status
    user = db.get(User, appt.user_id)
    env = _env(db, user, calendar=calendar)
    _claim(db, appt, previous)
    try:
        if previous == "needs_user_info":
            _clear_question(appt, superseded_by="confirm_link")
        finalize_confirmed(env, appt, chosen, via="confirm_link")
        audit(db, appt.user_id, "appointment.confirm", f"appointment:{appt.id}", {"slot_index": slot_index},
              actor="clinic")
        db.commit()
    except Exception:
        _release(db, appt, previous)
        raise
    return appt


def cancel_appointment(db: Session, user: User, appointment_id: str, calendar: Any = None,
                       email: Any = None) -> Appointment:
    """Patient cancels: status cancelled, calendar event deleted, the clinic is told by e-mail when
    a request was e-mailed. Raises LookupError when not found / not the user's."""
    appt = _own(db, user, appointment_id)
    if appt.status == "cancelled":
        return appt
    previous = appt.status
    env = _env(db, user, calendar=calendar, email=email)
    entries = [entry("cancelled", previous_status=previous, by="patient")]
    if appt.calendar_event_id:
        try:
            entries.append(entry("calendar_delete", event_id=appt.calendar_event_id,
                                 ok=bool(env.calendar.delete_event(appt.calendar_event_id))))
        except Exception as exc:
            entries.append(entry("calendar_delete", ok=False, error=str(exc)[:300]))
    request = (appt.clinic or {}).get("request") or {}
    if previous in ("requested", "needs_user_info", "confirmed") and request.get("to") and "@" in str(request["to"]):
        name = (user.profile.display_name if user.profile else None) or user.email
        subject = LABELS["cancel_subject"].format(patient=name)
        try:
            from app.services.booking.channels.email import booking_sender, reply_address

            res = booking_sender(env.email).send(request["to"], subject, f"<p>{LABELS['cancel_body']}</p>",
                                                 text=LABELS["cancel_body"],
                                                 reply_to=reply_address(appt.reply_token) or None,
                                                 tags={"kind": "booking_cancel", "appointment_id": appt.id})
            entries.append(entry("cancel_email", to=request["to"], accepted=bool(res.accepted)))
        except Exception as exc:
            entries.append(entry("cancel_email", to=request["to"], accepted=False, error=str(exc)[:300]))
    appt.status = "cancelled"
    append_log(appt, *entries)
    audit(db, user.id, "appointment.cancel", f"appointment:{appt.id}", {"previous_status": previous})
    db.commit()
    return appt


def list_appointments(db: Session, user_id: str, status: str | None = None, limit: int = 50) -> list[Appointment]:
    stmt = select(Appointment).where(Appointment.user_id == user_id)
    if status:
        stmt = stmt.where(Appointment.status == status)
    return list(db.scalars(stmt.order_by(Appointment.created_at.desc()).limit(max(1, min(limit, 200)))))


def get_appointment(db: Session, user_id: str, appointment_id: str) -> Appointment | None:
    row = db.get(Appointment, appointment_id)
    return row if row is not None and row.user_id == user_id else None


def list_messages(db: Session, user_id: str, appointment_id: str) -> list[AppointmentMessage]:
    return list(db.scalars(select(AppointmentMessage).where(AppointmentMessage.appointment_id == appointment_id,
                                                            AppointmentMessage.user_id == user_id)
                           .order_by(AppointmentMessage.created_at)))
