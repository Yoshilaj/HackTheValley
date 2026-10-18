"""Admin / dev helpers: POST /api/admin/seed-demo, POST /api/admin/seed-showcase (dev mode or X-Admin-Secret).

Seeds the demo user (``scripts.seed``: demo@example.com / demo1234, all consents) and the planted
35-day trend (``scripts.seed_demo``) so a live demo or ``scripts/demo_smoke.py`` can start from a
fresh database without shell access. Idempotent: a second call reports ``skipped: true``.
``seed-showcase`` builds the showcase account (``scripts.seed_showcase``: showcase@example.com /
showcase1234, overall risk red, a confirmed appointment).
``demo/{email|phone}`` sends one real booking e-mail / places one real AI call for the signed-in
user's latest appointment (the dev panel's presentation buttons; sandbox targets in sandbox mode).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_admin_or_dev
from app.core.security import audit
from app.db.models import Appointment, User
from app.db.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_or_dev)])


@router.post("/seed-demo")
def seed_demo(run_assessment: bool = False, db: Session = Depends(get_db)) -> dict:
    """Seed demo user + planted trend → ``{user_id, email, seed: {skipped, metrics, bp, logs}, assessments}``.
    With ``?run_assessment=true`` also runs ``assess_user`` (same as ``POST /api/risk/run``)."""
    from app.services.risk_service import assess_user
    from scripts.seed import DEMO_EMAIL, seed_demo_user
    from scripts.seed_demo import seed_planted_trend

    user = seed_demo_user(db)
    counts = seed_planted_trend(db, user)
    audit(db, user.id, "admin.seed_demo", f"user:{user.id}", counts, actor="admin", commit=True)
    assessments = []
    if run_assessment:
        assessments = [{"pathway": r.pathway, "level": r.level, "priority_score": r.priority_score}
                       for r in assess_user(db, user, trigger="manual")]
    return {"user_id": user.id, "email": DEMO_EMAIL, "seed": counts, "assessments": assessments}


@router.post("/seed-showcase")
def seed_showcase(db: Session = Depends(get_db)) -> dict:
    """Seed the showcase account → ``{user_id, email, seed, assessments, appointment}`` (idempotent)."""
    from scripts.seed_showcase import seed_showcase as run

    out = run(db)
    audit(db, out["user_id"], "admin.seed_showcase", f"user:{out['user_id']}", out["seed"], actor="admin", commit=True)
    return out


@router.get("/appointments/{appointment_id}/confirm-links")
def confirm_links(appointment_id: str, db: Session = Depends(get_db)) -> dict:
    """The clinic's confirm links for an appointment (what the booking e-mail contains), so a demo
    can play the clinic without a real inbox → ``{appointment_id, status, links: [{slot, start, url}]}``."""
    from fastapi import HTTPException

    from app.db.models import Appointment
    from app.services.booking_agent import confirm_url

    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise HTTPException(404, "Appointment not found")
    return {"appointment_id": appt.id, "status": appt.status,
            "links": [{"slot": i, "start": (s or {}).get("start"), "url": confirm_url(appt.id, i, (appt.clinic or {}).get("id"))}
                      for i, s in enumerate(appt.proposed_slots or [])]}


DEMO_PATHWAYS = ("cardio", "cognitive", "parkinsons", "general")


def _demo_appointment(db: Session, user: User) -> Appointment:
    """The user's latest appointment that has a clinic and proposed slots, else a new one planned
    by the booking agent for the first pathway with an assessment."""
    from app.services import followup

    for appt in db.scalars(select(Appointment).where(Appointment.user_id == user.id)
                           .order_by(Appointment.created_at.desc()).limit(20)):
        if appt.clinic and appt.proposed_slots:
            return appt
    for pathway in DEMO_PATHWAYS:
        try:
            appt = followup.start_for_pathway(db, user, pathway)
        except followup.NoAssessment:
            continue
        except followup.ConsentMissing as exc:
            raise HTTPException(403, "share_with_clinic consent is not active") from exc
        if appt is not None and appt.clinic and appt.proposed_slots:
            return appt
    raise HTTPException(409, "No appointment to demo: run a risk assessment first")


def _demo_fill_contact(clinic, channel: str) -> None:
    """Sandbox only: a clinic without an e-mail / phone of its own gets the sandbox target as its
    contact, so the demo can still show that channel (resolve_target refuses a missing contact)."""
    from app.core.config import settings
    from app.services.booking import sandbox

    if settings.booking_target_mode != "sandbox" or sandbox.real_detail(channel, clinic):
        return
    if channel == "email" and settings.sandbox_clinic_email:
        clinic.extra["detected_email"] = settings.sandbox_clinic_email
    elif channel == "phone" and settings.sandbox_clinic_phone:
        clinic.extra["international_phone"] = settings.sandbox_clinic_phone


@router.post("/demo/{channel}")
def trigger_demo(channel: Literal["email", "phone"], user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> dict:
    """Send the booking e-mail / place the AI call for the user's latest appointment right now,
    skipping the approval card → ``{appointment_id, clinic, channel, status, reason, to, external_ref}``.
    The appointment moves to ``requested`` so the reply / post-call webhook lands on it as usual."""
    from app.services.booking import orchestrator as orch
    from app.services.booking.common import append_log, entry

    appt = _demo_appointment(db, user)
    env = orch._env(db, user)
    ctx = orch._ctx_for(env, appt)
    _demo_fill_contact(ctx.clinic, channel)
    ch = env.channels.get(channel)
    reason = "not configured" if ch is None else ch.available(ctx.clinic, ctx)
    if reason is not None:
        raise HTTPException(409, f"{channel} channel unavailable: {reason}")
    result = ch.execute(appt, ctx)
    orch.store_messages(db, appt, result.messages)
    orch._record_attempt(appt, ctx.clinic.id, channel, result.status, result.reason, result.external_ref,
                         result.target, demo=True)
    append_log(appt, entry("channel", channel=channel, status=result.status, reason=result.reason,
                           external_ref=result.external_ref, details=result.details, demo=True))
    if result.status == "requested":
        orch._on_requested(env, appt, ctx, channel, result)
    audit(db, user.id, f"admin.demo_{channel}", f"appointment:{appt.id}",
          {"status": result.status, "target": (result.target or {}).get("value")}, actor="admin", commit=True)
    return {"appointment_id": appt.id, "clinic": ctx.clinic.name, "channel": channel, "status": result.status,
            "reason": result.reason, "to": (result.target or {}).get("value"), "external_ref": result.external_ref}
