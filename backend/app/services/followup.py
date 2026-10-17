"""Glue between risk assessment and the report / booking agent (BACKEND_PLAN C4 -> C6/C7).

    report, appointment = handle_followup(db, user, assessment)
    register_followup_hooks()   # called from app.main lifespan; idempotent

`app.services.risk_service` calls every hook in `FOLLOWUP_HOOKS` when a pathway newly becomes
`followup_recommended` (never for `emergency`). Our hook generates the clinician report and,
when the user consented to `share_with_clinic`, runs the booking agent. A pathway that already
has an active appointment (proposed / awaiting_approval / requested / needs_user_info / confirmed) from the last 30 days is not
booked again, so repeated nightly runs never spam clinics. The agent stops at
`awaiting_approval` (booking v2): nothing is sent to a clinic before the user approves.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import audit, has_consent
from app.db.models import Appointment, Report, RiskAssessment, User

log = get_logger(__name__)

ACTIVE_WINDOW = timedelta(days=30)
ACTIVE_STATUSES = ("proposed", "awaiting_approval", "processing", "requested", "needs_user_info", "confirmed")


def active_appointment_for_pathway(db: Session, user_id: str, pathway: str) -> Appointment | None:
    since = datetime.now(timezone.utc) - ACTIVE_WINDOW
    return db.scalar(
        select(Appointment).join(RiskAssessment, Appointment.assessment_id == RiskAssessment.id).where(
            Appointment.user_id == user_id, RiskAssessment.pathway == pathway,
            Appointment.status.in_(ACTIVE_STATUSES), Appointment.created_at >= since,
        ).order_by(Appointment.created_at.desc()).limit(1))


def handle_followup(db: Session, user: User, assessment: RiskAssessment, *, llm: Any = None, calendar: Any = None,
                    places: Any = None, email: Any = None, language: str | None = None,
                    commit: bool = True) -> tuple[Report | None, Appointment | None]:
    """generate_report -> run_booking_agent (consent-gated) -> (report, appointment | None).
    Emergency / non-followup assessments are ignored (returns (None, None))."""
    from app.providers.llm import get_llm_provider
    from app.services import llm_audit_hook
    from app.services.booking_agent import run_booking_agent
    from app.services.report_service import generate_report

    if assessment.level != "followup_recommended":
        log.info("handle_followup ignored", extra={"level": assessment.level, "assessment_id": assessment.id})
        return None, None
    llm = llm or get_llm_provider(audit_hook=llm_audit_hook(db, user.id, f"risk_assessment:{assessment.id}"))
    report = generate_report(db, user, assessment, llm, language, commit=False)

    appointment: Appointment | None = None
    existing = active_appointment_for_pathway(db, user.id, assessment.pathway)
    if existing is not None:
        audit(db, user.id, "booking.skipped", f"risk_assessment:{assessment.id}",
              {"reason": "active_appointment", "appointment_id": existing.id}, actor="agent")
    elif not has_consent(db, user.id, "share_with_clinic"):
        audit(db, user.id, "booking.skipped", f"risk_assessment:{assessment.id}",
              {"reason": "no_consent:share_with_clinic"}, actor="agent")
    else:
        appointment = run_booking_agent(db, user, assessment, llm, calendar, places, email, language,
                                        report=report, commit=False)
    if commit:
        db.commit()
    else:
        db.flush()
    return report, appointment


def followup_hook(db: Session, user: User, assessment: RiskAssessment) -> None:
    """FOLLOWUP_HOOKS entry. Commits so the approval card (and, with approval off, the record of
    the clinic contact) persists even if a later step of the caller fails."""
    handle_followup(db, user, assessment, commit=True)


def register_followup_hooks() -> bool:
    """Append `followup_hook` to `app.services.risk_service.FOLLOWUP_HOOKS` (once).
    Returns False (and logs a warning) when risk_service is not importable."""
    try:
        from app.services.risk_service import FOLLOWUP_HOOKS
    except ImportError as exc:
        log.warning("risk_service.FOLLOWUP_HOOKS not available; follow-up hook not registered",
                    extra={"error": str(exc)})
        return False
    if followup_hook not in FOLLOWUP_HOOKS:
        FOLLOWUP_HOOKS.append(followup_hook)
    return True
