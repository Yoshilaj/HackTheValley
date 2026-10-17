"""Appointment follow-ups (job `appointment_followups`, daily 09:00; booking-v2 spec §2).

For every `requested` appointment:
- `share_with_clinic` consent revoked → `cancelled` (+ user notified; nothing sent to the clinic).
- ≥ 10 days since the first contact with the current clinic → `failed` + `appointment_failed`.
- ≥ 3 days since the last contact without any inbound reply → ONE follow-up per clinic: the next
  usable channel in the plan that was not tried yet (e.g. phone after e-mail), otherwise a
  reminder on the same channel (e-mail reminder in the same thread, phone: a new call). A web form
  is never re-submitted as a reminder: the user is notified instead.
Times come from `channel_attempts` (successful attempts for the current clinic), falling back to
`created_at`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import audit, has_consent
from app.db.models import Appointment, AppointmentMessage, User
from app.services import notify as notify_svc
from app.services.booking.channels import CHANNEL_ORDER
from app.services.booking.common import append_log, entry, utcnow

log = get_logger(__name__)

REMINDER_AFTER = timedelta(days=3)
FAIL_AFTER = timedelta(days=10)
FOLLOWUP_TYPES = ("reminder", "next_channel")


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def contact_times(appt: Appointment) -> tuple[datetime, datetime]:
    """(first, last) successful contact with the current clinic."""
    cid = (appt.clinic or {}).get("id")
    times = [t for a in appt.channel_attempts or [] if a.get("clinic_id") == cid
             and a.get("status") in ("requested", "confirmed") and (t := _ts(a.get("ts"))) is not None]
    if not times:
        return appt.created_at, appt.created_at
    return min(times), max(times)


def _aware(dt: datetime) -> datetime:
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def process_appointment_followups(db: Session, now: datetime | None = None, email: Any = None,
                                  calendar: Any = None, channels: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.services.booking.orchestrator import _env, execute_plan

    now = now or utcnow()
    counts = {"checked": 0, "reminded": 0, "next_channel": 0, "failed": 0, "cancelled": 0}
    for appt in list(db.scalars(select(Appointment).where(Appointment.status == "requested"))):
        counts["checked"] += 1
        user = db.get(User, appt.user_id)
        if user is None:
            continue
        clinic_name = (appt.clinic or {}).get("name", "the clinic")
        if not has_consent(db, appt.user_id, "share_with_clinic"):
            appt.status = "cancelled"
            append_log(appt, entry("cancelled", by="system", reason="share_with_clinic consent revoked"))
            notify_svc.notify(db, user.id, notify_svc.KIND_APPOINTMENT_FAILED,
                              f"{clinic_name}: appointment request cancelled",
                              "You withdrew consent to share with clinics, so no reminder was sent and the "
                              "appointment request was cancelled.",
                              payload={"appointment_id": appt.id, "reason": "consent_revoked"}, email=True, commit=False)
            audit(db, user.id, "booking.cancelled_consent_revoked", f"appointment:{appt.id}", actor="system")
            counts["cancelled"] += 1
            continue
        first, last = (_aware(t) for t in contact_times(appt))
        if now - first >= FAIL_AFTER:
            appt.status = "failed"
            append_log(appt, entry("failed", reason="no confirmation after 10 days"))
            notify_svc.notify(db, user.id, notify_svc.KIND_APPOINTMENT_FAILED, f"No reply from {clinic_name}",
                              f"{clinic_name} has not confirmed the request after 10 days. You may want to call "
                              "them or try another clinic.",
                              payload={"appointment_id": appt.id, "reason": "no_confirmation_10d"},
                              email=True, commit=False)
            counts["failed"] += 1
            continue
        if now - last < REMINDER_AFTER:
            continue
        cid = (appt.clinic or {}).get("id")
        if any(e.get("type") in FOLLOWUP_TYPES and e.get("clinic_id") == cid for e in appt.agent_log or []):
            continue  # one follow-up per clinic
        replied = db.scalar(select(AppointmentMessage.id).where(AppointmentMessage.appointment_id == appt.id,
                                                                AppointmentMessage.direction == "in",
                                                                AppointmentMessage.created_at >= last))
        if replied:
            continue
        env = _env(db, user, calendar=calendar, email=email, channels=channels, now=now)
        tried = {a.get("channel") for a in appt.channel_attempts or []
                 if a.get("clinic_id") == cid and a.get("status") in ("requested", "confirmed", "failed")}
        current = appt.channel
        later = ([c for c in CHANNEL_ORDER if CHANNEL_ORDER.index(c) > CHANNEL_ORDER.index(current) and c not in tried]
                 if current in CHANNEL_ORDER else [])
        result = execute_plan(env, appt, only=later) if later else None
        if result is not None:
            append_log(appt, entry("next_channel", clinic_id=cid, channel=appt.channel, status=result.status))
            counts["next_channel"] += 1
            continue
        if current == "web":
            # a reminder would re-submit the clinic's web form (a duplicate booking): never
            append_log(appt, entry("reminder", clinic_id=cid, channel="web", accepted=False,
                                   reason="web form is never re-submitted as a reminder"))
            notify_svc.notify(db, user.id, notify_svc.KIND_APPOINTMENT_FAILED, f"No reply yet from {clinic_name}",
                              f"We sent your request through {clinic_name}'s online form 3 days ago and have not heard "
                              "back. We cannot send a reminder through the form; you may want to call the clinic.",
                              payload={"appointment_id": appt.id, "reason": "no_reply_web_form"}, email=True,
                              commit=False)
            counts["reminded"] += 1
            continue
        if current:
            result = execute_plan(env, appt, only=[current], reminder=True)
            ok = result is not None
            append_log(appt, entry("reminder", clinic_id=cid, channel=current, accepted=ok,
                                   to=((appt.clinic or {}).get("request") or {}).get("to"),
                                   external_ref=result.external_ref if result else None))
            audit(db, user.id, "booking.reminder_sent", f"appointment:{appt.id}", {"accepted": ok}, actor="agent")
            counts["reminded"] += 1
    db.flush()
    return counts
