"""Tiny background-job registry. Every run is recorded in the `Job` table.

Register a job in YOUR OWN module and import that module in `app/jobs/__init__.py`:

    from app.jobs.registry import job

    @job("nightly_risk_assessment", cron="0 3 * * *")
    def nightly_risk_assessment(db) -> dict:
        ...                      # db is a fresh SessionLocal session; commit is done for you
        return {"users": 12}     # stored in Job.result (must be JSON-serialisable)

Reserved hook names for other owners (not registered here yet):
- `nightly_risk_assessment`  (C4/C5, app/services/risk*)   e.g. cron="0 3 * * *"
- `process_pending_uploads`  (C1, app/services/ingest*)    e.g. interval_minutes=60
- `appointment_followups`    (C6, app/services/booking*)   e.g. interval_minutes=60

Built-ins registered here: `cleanup_expired_jobs`, `weekly_summary_notifications`.

`run_job(name)` creates a Job row (status running -> done | failed), runs the function in
its own session, records result / error, and audits. It refuses (JobAlreadyRunning) to start
a job whose previous run is still 'running' and started less than 1 hour ago.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import audit
from app.db import session as db_session
from app.db.models import Job

log = get_logger(__name__)

JobFn = Callable[[Session], dict[str, Any] | None]

STATUS_PENDING = "pending"  # queued (e.g. Apple Health import BackgroundTask)
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STALE_AFTER = timedelta(hours=1)

# Hook names other modules are expected to register (documented, not enforced).
HOOK_NIGHTLY_RISK_ASSESSMENT = "nightly_risk_assessment"
HOOK_PROCESS_PENDING_UPLOADS = "process_pending_uploads"
HOOK_APPOINTMENT_FOLLOWUPS = "appointment_followups"


@dataclass(frozen=True)
class JobSpec:
    name: str
    fn: JobFn
    interval_minutes: float | None = None
    cron: str | None = None  # standard 5-field crontab, evaluated in the scheduler timezone

    @property
    def schedule(self) -> str:
        return f"cron:{self.cron}" if self.cron else f"every {self.interval_minutes} min" if self.interval_minutes else "manual"


JOBS: dict[str, JobSpec] = {}


class UnknownJob(KeyError):
    pass


class JobAlreadyRunning(RuntimeError):
    def __init__(self, name: str, job_id: str) -> None:
        super().__init__(f"job {name!r} is already running (Job {job_id})")
        self.name = name
        self.job_id = job_id


def job(name: str, *, interval_minutes: float | None = None, cron: str | None = None) -> Callable[[JobFn], JobFn]:
    """Register `fn(db) -> dict` under `name`. Give at most one of interval_minutes / cron
    (neither = manual-only, runnable via POST /api/jobs/{name}/run)."""
    if interval_minutes is not None and cron is not None:
        raise ValueError("give interval_minutes OR cron, not both")
    if interval_minutes is not None and interval_minutes <= 0:
        raise ValueError("interval_minutes must be > 0")

    def decorator(fn: JobFn) -> JobFn:
        JOBS[name] = JobSpec(name=name, fn=fn, interval_minutes=interval_minutes, cron=cron)
        return fn

    return decorator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _running_guard(db: Session, name: str) -> Job | None:
    """The most recent run of `name` if it is still 'running' and started < STALE_AFTER ago."""
    last = db.scalar(select(Job).where(Job.name == name).order_by(Job.created_at.desc()).limit(1))
    if last is None or last.status != STATUS_RUNNING or last.started_at is None:
        return None
    return last if _utcnow() - last.started_at < STALE_AFTER else None


def run_job(name: str, db: Session | None = None, scheduled_for: datetime | None = None) -> Job:
    """Run a registered job synchronously and return its (committed) Job row.

    `db` is only used for the Job bookkeeping row + audit; the job function always gets its
    own `SessionLocal()` session, committed on success and rolled back on failure.
    Raises UnknownJob / JobAlreadyRunning; job exceptions are caught and recorded (status=failed).
    """
    spec = JOBS.get(name)
    if spec is None:
        raise UnknownJob(name)

    own_db = db is None
    book = db or db_session.SessionLocal()
    try:
        running = _running_guard(book, name)
        if running is not None:
            raise JobAlreadyRunning(name, running.id)

        now = _utcnow()
        row = Job(name=name, scheduled_for=scheduled_for or now, started_at=now, status=STATUS_RUNNING)
        book.add(row)
        book.commit()

        work = db_session.SessionLocal()
        try:
            result = spec.fn(work) or {}
            work.commit()
            row.status, row.result = STATUS_DONE, result
        except Exception as exc:
            work.rollback()
            log.exception("job failed", extra={"job": name})
            row.status = STATUS_FAILED
            row.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"[:4000]
        finally:
            work.close()

        row.finished_at = _utcnow()
        audit(book, None, "job.run", f"job:{name}", {"job_id": row.id, "status": row.status}, actor="system")
        book.commit()
        return row
    finally:
        if own_db:
            book.close()


def recent_jobs(db: Session, limit: int = 50, name: str | None = None) -> list[Job]:
    stmt = select(Job)
    if name:
        stmt = stmt.where(Job.name == name)
    return list(db.scalars(stmt.order_by(Job.created_at.desc()).limit(max(1, min(limit, 500)))))


# ---------------------------------------------------------------- built-in jobs

@job("cleanup_expired_jobs", cron="30 4 * * *")
def cleanup_expired_jobs(db: Session) -> dict[str, Any]:
    """Placeholder: report Job row counts by status (no deletion yet)."""
    rows = db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    return {"deleted": 0, "by_status": {status: count for status, count in rows}}


@job("weekly_summary_notifications", cron="0 9 * * 1")
def weekly_summary_notifications(db: Session) -> dict[str, Any]:
    """Placeholder: one in-app `weekly_summary` notification per user (no email)."""
    from app.db.models import User
    from app.services.notify import KIND_WEEKLY_SUMMARY, notify

    users = list(db.scalars(select(User)))
    for user in users:
        ja = ((user.profile.language if user.profile else None) or "en").startswith("ja")
        notify(
            db, user.id, KIND_WEEKLY_SUMMARY,
            "今週のまとめ" if ja else "Your weekly summary",
            "今週の記録をダッシュボードで確認しましょう。" if ja else "Review this week's records on your dashboard.",
            commit=False,
        )
    return {"notified": len(users)}
