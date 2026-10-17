"""Shared helpers for the booking agent: log entries, confirm-link tokens, labels, clinic e-mail.

Canada only, English only (BACKEND_PLAN revision 2026-10-17). The clinic-facing text is a fixed
template: the LLM never writes free text that reaches a clinic, so no health data can leak.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.integrations import Slot
from app.services.booking.channels.base import PatientInfo

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
DEFAULT_TZ = "America/Toronto"

from app.services.notify import KIND_APPOINTMENT_APPROVAL_NEEDED, KIND_APPOINTMENT_NEEDS_INFO  # noqa: E402,F401

#: Generic reason category per pathway (the only "why" a clinic sees; details sit behind the report link).
REASONS = {
    "cardio": "Heart and blood-pressure check-up",
    "cognitive": "Memory consultation",
    "parkinsons": "Neurology consultation (movement changes)",
    "pd": "Neurology consultation (movement changes)",
    "general": "General check-up",
}
CLINIC_NOTES = {
    "cardio": "The patient would like to book a check-up; details are in the linked report.",
    "cognitive": "The patient would like to book a memory consultation; details are in the linked report.",
    "parkinsons": "The patient would like to book a neurology consultation; details are in the linked report.",
    "general": "The patient would like to book a general check-up; details are in the linked report.",
}
SLOT_PREFERENCES = ("morning", "afternoon", "any")
SLOT_PREFERENCE_TEXT = {"morning": "Morning appointments are preferred.",
                        "afternoon": "Afternoon appointments are preferred."}

LABELS = {
    "subject": "Appointment request for {patient}",
    "reminder_subject": "Reminder: appointment request for {patient}",
    "reminder": "Reminder — we have not yet received a reply to this request.",
    "heading": "Appointment request", "greeting": "Dear",
    "intro": ("This request is sent by an AI health assistant on behalf of the patient below, who has consented "
              "to share a referral report with your clinic. Please choose one of the proposed times, or reply to "
              "this e-mail."),
    "patient": "Patient", "dob": "Date of birth", "phone": "Phone", "email": "E-mail", "postal_code": "Postal code",
    "address": "Address", "reason": "Reason for visit", "note": "Note",
    "report": "Referral report (secure link)", "slots": "Proposed times", "confirm": "Confirm this time",
    "other_time": "If none of these times work, simply reply to this e-mail with another time or any questions",
    "referral": ("If your clinic needs a referral from a family doctor first, please reply and say so; the linked "
                 "report can support that referral."),
    "footer": ("The linked report is generated from home measurements and self-reported information; it is not a "
               "diagnosis. Please do not forward this e-mail."),
    "cancel_subject": "Cancelled: appointment request for {patient}",
    "cancel_body": "The patient has cancelled this appointment request. No action is needed.",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def entry(type_: str, **kw: Any) -> dict[str, Any]:
    """One agent_log entry {ts, type, …}."""
    return json_safe({"ts": utcnow().isoformat(), "type": type_, **kw})


def append_log(appt: Any, *entries: dict[str, Any]) -> None:
    appt.agent_log = list(appt.agent_log or []) + list(entries)


def tz_of(user: Any) -> str:
    tz = (getattr(user.profile, "timezone", None) if user is not None and user.profile else None) or DEFAULT_TZ
    try:
        ZoneInfo(tz)
        return tz
    except Exception:
        return DEFAULT_TZ


# ---------------------------------------------------------------- confirm-link tokens

CONFIRM_TOKEN_TTL = timedelta(days=30)


def _token_secret() -> bytes:
    """APPOINTMENT_TOKEN_SECRET, or a key derived from JWT_SECRET with a fixed label."""
    if settings.appointment_token_secret:
        return settings.appointment_token_secret.encode()
    return hmac.new(settings.jwt_secret.encode(), b"appointment-confirm-token-key-v1", hashlib.sha256).digest()


def _binding(clinic_id: str | None, slot_index: int | None) -> str:
    return f"{clinic_id or '-'}:{'-' if slot_index is None else int(slot_index)}"


def token_sig(appointment_id: str, ts: int, clinic_id: str | None = None, slot_index: int | None = None) -> str:
    """HMAC over the appointment id, the clinic the request went to, the slot index and the issue
    time: a link from an earlier clinic (after fall-through) or for another slot never verifies."""
    msg = f"appointment-confirm:{appointment_id}:{_binding(clinic_id, slot_index)}:{ts}"
    return hmac.new(_token_secret(), msg.encode(), hashlib.sha256).hexdigest()[:40]


def confirm_token(appointment_id: str, issued_at: datetime | None = None, *, clinic_id: str | None = None,
                  slot_index: int | None = None) -> str:
    """``<unix ts>.<HMAC-SHA256>`` over appointment id + clinic id + slot index + issue time (30 days)."""
    ts = int((issued_at or utcnow()).timestamp())
    return f"{ts}.{token_sig(appointment_id, ts, clinic_id, slot_index)}"


def verify_confirm_token(appointment_id: str, token: str | None, now: datetime | None = None, *,
                         clinic_id: str | None = None, slot_index: int | None = None) -> bool:
    if not token or "." not in token:
        return False
    ts_s, sig = token.split(".", 1)
    try:
        ts = int(ts_s)
    except ValueError:
        return False
    age = (now or utcnow()).timestamp() - ts
    if age > CONFIRM_TOKEN_TTL.total_seconds() or age < -300:
        return False
    return hmac.compare_digest(token_sig(appointment_id, ts, clinic_id, slot_index), sig)


def confirm_token_for(appt: Any, slot_index: int, issued_at: datetime | None = None) -> str:
    """confirm_token bound to the appointment's CURRENT clinic (what the clinic e-mail carries)."""
    return confirm_token(appt.id, issued_at, clinic_id=(appt.clinic or {}).get("id"), slot_index=slot_index)


def confirm_url(appointment_id: str, slot_index: int, clinic_id: str | None = None) -> str:
    """The clinic's confirm link. GET shows the time + a "Confirm" button; only POST confirms."""
    token = confirm_token(appointment_id, clinic_id=clinic_id, slot_index=slot_index)
    return (f"{settings.base_url.rstrip('/')}/api/appointments/{appointment_id}/confirm"
            f"?slot={slot_index}&token={token}")


# ---------------------------------------------------------------- labels

def slot_label(slot: Slot, tz: str | None) -> str:
    """'Tue Oct 21, 2026, 10:00 AM–10:30 AM EDT' in `tz`."""
    zone = ZoneInfo(tz or DEFAULT_TZ)
    s, e = slot.start.astimezone(zone), slot.end.astimezone(zone)
    hm = lambda d: f"{d:%I:%M %p}".lstrip("0")  # noqa: E731
    return f"{s:%a %b} {s.day}, {s:%Y}, {hm(s)}–{hm(e)} {s.tzname()}"


def clinic_note(pathway: str, language: str = "en", preference: Any = None) -> str:
    """Fixed template note for the clinic; ``preference`` outside SLOT_PREFERENCES is ignored."""
    note = CLINIC_NOTES.get(pathway, CLINIC_NOTES["general"])
    pref = SLOT_PREFERENCE_TEXT.get(str(preference or "").strip().lower())
    return f"{note} {pref}" if pref else note


def reason_for(pathway: str) -> str:
    return REASONS.get(pathway, REASONS["general"])


def report_url_for(report: Any) -> str:
    return settings.base_url.rstrip("/") + "/r/" + report.share_token if report is not None and report.share_token else ""


LANGUAGE_NAMES = {"en": "English", "fr": "French", "es": "Spanish", "pt": "Portuguese", "it": "Italian",
                  "de": "German", "zh": "Chinese", "yue": "Cantonese", "cmn": "Mandarin", "pa": "Punjabi",
                  "hi": "Hindi", "ur": "Urdu", "ta": "Tamil", "tl": "Tagalog", "ar": "Arabic", "fa": "Persian",
                  "ko": "Korean", "ja": "Japanese", "vi": "Vietnamese", "ru": "Russian", "uk": "Ukrainian",
                  "pl": "Polish"}


def language_name(code: str | None) -> str | None:
    if not code:
        return None
    return LANGUAGE_NAMES.get(str(code).strip().lower().split("-")[0], str(code))


def patient_email(user: Any) -> str | None:
    p = getattr(user, "profile", None)
    return (getattr(p, "contact_email", None) if p else None) or getattr(user, "email", None)


def patient_info(user: Any, pathway: str, slots: list[Slot], clinic_tz: str, report_url: str) -> PatientInfo:
    """The allow-listed patient fields a clinic may receive (spec §0): identity / contact details
    from onboarding, the generic reason category, slots and the report link — no health data."""
    p = user.profile
    g = (lambda k: getattr(p, k, None) if p is not None else None)  # noqa: E731
    dob = g("date_of_birth")
    fd = g("family_doctor") if isinstance(g("family_doctor"), dict) else {}
    legal = getattr(p, "legal_name", None) if p is not None else None
    return PatientInfo(
        name=legal or g("display_name") or user.email,
        first_name=g("first_name"), last_name=g("last_name"),
        date_of_birth=dob.isoformat() if dob else None,
        sex=g("sex"),
        phone=g("phone"),
        email=patient_email(user),
        postal_code=g("postal_code"),
        address=getattr(p, "full_address", None) if p is not None else None,
        street=g("street"), city=g("city"), province=g("province"),
        family_doctor_name=(fd.get("name") or None), family_doctor_clinic=(fd.get("clinic") or None),
        family_doctor_phone=(fd.get("phone") or None),
        has_referral=g("has_referral"),
        preferred_language=language_name(g("language")),
        reason_category=reason_for(pathway),
        preferred_slots=[slot_label(s, clinic_tz) for s in slots],
        report_url=report_url or None,
    )


#: Onboarding fields a clinic routinely asks for when booking (GET /api/profile/booking-readiness).
REQUIRED_BOOKING_FIELDS = ("first_name", "last_name", "date_of_birth", "sex", "phone", "email", "street", "city",
                           "province", "postal_code")
RECOMMENDED_BOOKING_FIELDS = ("family_doctor", "has_referral")
#: What a clinic may receive (profile field names, for the onboarding / approval UI).
FIELDS_SHARED_WITH_CLINICS = ("first_name", "last_name", "date_of_birth", "sex", "phone", "email", "street", "city",
                              "province", "postal_code", "family_doctor", "has_referral", "language",
                              "reason_category", "preferred_slots", "report_url")
NEVER_SHARED_WITH_CLINICS = ("health_card_number", "medical_history", "conditions", "measurements", "medications",
                             "diagnoses", "insurance")


def missing_profile_fields(user: Any) -> list[str]:
    """Required booking fields still empty — what the booking agent could not answer if asked."""
    p = getattr(user, "profile", None)
    g = (lambda k: getattr(p, k, None) if p is not None else None)  # noqa: E731
    has_addr = bool((g("address") or "").strip())
    present = {
        "first_name": g("first_name"), "last_name": g("last_name"), "date_of_birth": g("date_of_birth"),
        "sex": g("sex"), "phone": g("phone"), "email": patient_email(user),
        "street": g("street") or has_addr, "city": g("city") or has_addr,
        "province": g("province"), "postal_code": g("postal_code"),
    }
    return [k for k in REQUIRED_BOOKING_FIELDS if not present[k]]


def booking_readiness(user: Any) -> dict[str, Any]:
    p = getattr(user, "profile", None)
    fd = getattr(p, "family_doctor", None) if p is not None else None
    missing = missing_profile_fields(user)
    recommended = [k for k, v in (("family_doctor", isinstance(fd, dict) and fd.get("name")),
                                  ("has_referral", getattr(p, "has_referral", None) is not None if p else False))
                   if not v]
    return {"ready": not missing, "missing": missing, "recommended": recommended,
            "fields_shared_with_clinics": list(FIELDS_SHARED_WITH_CLINICS),
            "never_shared": list(NEVER_SHARED_WITH_CLINICS)}


# ---------------------------------------------------------------- clinic e-mail

def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=select_autoescape(["html", "j2"]),
                       trim_blocks=True, lstrip_blocks=True)


def render_booking_email(*, appointment_id: str, clinic_name: str, patient: PatientInfo, note: str | None,
                         slots: list[Slot], tz: str, reply_to: str | None, reminder: bool = False,
                         sandbox_note: str | None = None, language: str = "en", clinic_id: str | None = None,
                         **_: Any) -> tuple[str, str, str]:
    """(subject, html, text) for the clinic e-mail (app/templates/booking_email.html.j2)."""
    L = LABELS
    rows = [{"label": slot_label(s, tz), "confirm_url": confirm_url(appointment_id, i, clinic_id)} for i, s in enumerate(slots)]
    # Minimal on purpose: name + date of birth identify the patient; contact + postal code. The full
    # address only when there is no postal code; sex / family doctor / street parts never by e-mail.
    shared = patient.shared_fields()
    if shared.get("postal_code"):
        shared.pop("address", None)
    details = [(L[k if k != "date_of_birth" else "dob"], shared[k]) for k in
               ("date_of_birth", "phone", "email", "postal_code", "address") if shared.get(k)]
    html = _env().get_template("booking_email.html.j2").render(
        L=L, clinic_name=clinic_name, patient_name=patient.name, details=details, reason=patient.reason_category,
        note=note, report_url=patient.report_url or "", slots=rows, reply_to=reply_to, reminder=reminder,
        sandbox_note=sandbox_note)
    subject = (L["reminder_subject"] if reminder else L["subject"]).format(patient=patient.name)
    if sandbox_note:
        subject = "[SANDBOX] " + subject
    lines = [f"[{sandbox_note}]", ""] if sandbox_note else []
    lines += [L["reminder"]] if reminder else []
    lines += [L["intro"], "", f"{L['patient']}: {patient.name}"]
    lines += [f"{k}: {v}" for k, v in details]
    lines += [f"{L['reason']}: {patient.reason_category}"]
    if note:
        lines.append(f"{L['note']}: {note}")
    lines += ["", f"{L['report']}: {patient.report_url or ''}", "", L["slots"] + ":"]
    lines += [f"{i + 1}. {r['label']} — {L['confirm']}: {r['confirm_url']}" for i, r in enumerate(rows)]
    lines += ["", L["other_time"] + (f" ({reply_to})" if reply_to else "") + ".", L["referral"], "", L["footer"]]
    return subject, html, "\n".join(lines)
