"""Risk assessment endpoints (thin; logic in app/services/risk_service.py, dashboard_service.py)."""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, get_optional_user
from app.core.config import settings
from app.core.security import audit
from app.db.models import RiskAssessment, User
from app.db.session import get_db
from app.schemas.dashboard import PathwayCard
from app.jobs.scheduler import scheduler_status
from app.schemas.risk import RiskAssessmentOut, RiskHistoryPoint, RiskRunOut, RiskStatusOut
from app.services import dashboard_service, risk_service
from app.services.context_builder import user_zone

router = APIRouter(prefix="/risk", tags=["risk"])

STATUS_JOBS = ("nightly_risk_assessment", "weekly_summary_notifications")


@router.get("/latest", response_model=list[PathwayCard])
def latest(language: str | None = Query(None, pattern="^(ja|en)$"),
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Latest §11.4 card per pathway (assessed on demand if none exist)."""
    cards = dashboard_service.latest_cards(db, user, language)
    audit(db, user.id, "risk.read_latest", f"user:{user.id}", commit=True)
    return cards


@router.get("/history", response_model=list[RiskHistoryPoint])
def history(weeks: int = Query(8, ge=1, le=52), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Weekly points ``[{week, pathway, level, priority_score, assessment_id}]``, oldest first."""
    return risk_service.assessment_history(db, user.id, weeks=weeks, tz=user_zone(user.profile))


@router.post("/run", response_model=RiskRunOut)
def run(
    user_id: str | None = None,
    user: User | None = Depends(get_optional_user),
    x_admin_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Assess the current user now; with ``?user_id=`` an admin (X-Admin-Secret) may assess anyone."""
    if user_id is not None and (user is None or user_id != user.id):
        if not x_admin_secret or not hmac.compare_digest(x_admin_secret, settings.admin_secret):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin secret required")
        target = db.get(User, user_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        actor = "admin"
    elif user is not None:
        target, actor = user, "user"
    else:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    rows = risk_service.assess_user(db, target, trigger="manual")
    audit(db, target.id, "risk.run", f"user:{target.id}", {"by": actor}, actor=actor, commit=True)
    return RiskRunOut(user_id=target.id, trigger="manual", assessments=rows)


@router.get("/status", response_model=RiskStatusOut)  # declared before /{assessment_id}
def risk_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Newest assessment time + trigger, and the next scheduled nightly / weekly run (null when stopped)."""
    rows = risk_service.latest_assessments(db, user.id).values()
    newest = max(rows, key=lambda r: r.created_at, default=None)
    sched = scheduler_status()
    next_runs = {j["name"]: j["next_run"] for j in sched["jobs"] if j["name"] in STATUS_JOBS}
    return RiskStatusOut(
        last_assessed_at=newest.created_at if newest else None,
        last_trigger=(newest.data_quality or {}).get("trigger") if newest else None,
        scheduler_running=bool(sched["running"]),
        next_runs={name: next_runs.get(name) for name in STATUS_JOBS},
    )


@router.get("/{assessment_id}", response_model=RiskAssessmentOut)
def get_one(assessment_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(RiskAssessment, assessment_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")
    audit(db, user.id, "risk.read", f"risk_assessment:{row.id}", commit=True)
    return row
