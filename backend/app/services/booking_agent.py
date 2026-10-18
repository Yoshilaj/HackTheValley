"""Appointment-booking agent — public API (implementation in app/services/booking/).

    appt = run_booking_agent(db, user, assessment, llm, calendar, places, email)  # -> awaiting_approval
    appt = approve_appointment(db, user, appt.id)          # runs web → email → phone
    appt = decline_appointment(db, user, appt.id)
    appt = submit_user_info(db, user, appt.id, answer="…", accept=None)
    appt = confirm_appointment(db, appt.id, 0, confirm_token_for(appt, 0))   # clinic link (POST)
    appt = act_by_link(db, appt.id, "approve", token)   # e-mail buttons: approve | decline | calendar
    appt = add_to_calendar(db, user, appt.id)          # user-approved Google Calendar write
    appt = skip_calendar(db, user, appt.id)
    appt = cancel_appointment(db, user, appt.id)
    process_appointment_followups(db, now=...)             # job 'appointment_followups' (daily 09:00)

See docs/specs/booking-v2.md and app/services/booking/orchestrator.py for the flow.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings  # noqa: F401  (tests monkeypatch ba.settings)
from app.jobs.registry import HOOK_APPOINTMENT_FOLLOWUPS, job
from app.services.booking.common import (  # noqa: F401
    CLINIC_NOTES, LABELS, REASONS, SLOT_PREFERENCES, clinic_note, confirm_token, confirm_token_for, confirm_url, render_booking_email,
    slot_label, token_sig as _token_sig, user_action_token, user_action_url, verify_confirm_token, verify_user_action_token,
)
from app.services.booking.followups import FAIL_AFTER, REMINDER_AFTER, process_appointment_followups  # noqa: F401
from app.services.booking.orchestrator import (  # noqa: F401
    ACTIVE_STATUSES, OPEN_STATUSES, AppointmentStateError, act_by_link, add_to_calendar, apply_outcome, approve_appointment,
    calendar_status_for, cancel_appointment, confirm_appointment, confirm_preview, decline_appointment, get_appointment,
    ics_by_link, link_preview, list_appointments, list_messages, run_booking_agent, skip_calendar, submit_user_info,
)


@job(HOOK_APPOINTMENT_FOLLOWUPS, cron="0 9 * * *")
def appointment_followups(db: Session) -> dict[str, Any]:
    """Daily 09:00: next channel / reminder after 3 days, 'failed' after 10 days."""
    return process_appointment_followups(db)
