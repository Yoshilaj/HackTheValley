"""Admin / dev helpers: POST /api/admin/seed-demo, POST /api/admin/seed-showcase (dev mode or X-Admin-Secret).

Seeds the demo user (``scripts.seed``: demo@example.com / demo1234, all consents) and the planted
35-day trend (``scripts.seed_demo``) so a live demo or ``scripts/demo_smoke.py`` can start from a
fresh database without shell access. Idempotent: a second call reports ``skipped: true``.
``seed-showcase`` builds the showcase account (``scripts.seed_showcase``: showcase@example.com /
showcase1234, overall risk red, a confirmed appointment).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import require_admin_or_dev
from app.core.security import audit
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
