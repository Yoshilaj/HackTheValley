"""Admin-only job endpoints (X-Admin-Secret)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.db.session import get_db
from app.jobs import JobAlreadyRunning, UnknownJob, run_job
from app.jobs.registry import recent_jobs
from app.jobs.scheduler import scheduler_status
from app.schemas.notifications import JobOut, SchedulerStatusOut

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[JobOut])
def list_jobs(limit: int = Query(50, ge=1, le=500), name: str | None = None, db: Session = Depends(get_db)):
    """Recent Job rows, newest first."""
    return recent_jobs(db, limit=limit, name=name)


@router.get("/scheduler", response_model=SchedulerStatusOut)
def get_scheduler_status():
    return scheduler_status()


@router.post("/{name}/run", response_model=JobOut)
def run(name: str, db: Session = Depends(get_db)):
    """Run a registered job synchronously; returns its Job row (status done|failed)."""
    try:
        return run_job(name, db=db)
    except UnknownJob:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown job {name!r}") from None
    except JobAlreadyRunning as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
