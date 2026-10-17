"""In-process APScheduler (BackgroundScheduler) running the jobs in app/jobs/registry.py.

SINGLE-PROCESS ASSUMPTION: the scheduler lives inside the API process, so deploy with ONE
uvicorn worker (`uvicorn app.main:app --workers 1`); N workers would run every job N times.
The registry's running-guard only protects against overlapping runs, not duplicate schedulers.
Started from app.main's lifespan only when settings.scheduler_enabled (false in tests).
Timezone: settings.scheduler_timezone if defined, else America/Toronto.
"""

from __future__ import annotations

import threading
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logging import get_logger
from app.jobs.registry import JOBS, JobAlreadyRunning, JobSpec, run_job

log = get_logger(__name__)

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


def _timezone() -> str:
    return getattr(settings, "scheduler_timezone", None) or "America/Toronto"


def _trigger(spec: JobSpec, tz: str):
    if spec.cron:
        return CronTrigger.from_crontab(spec.cron, timezone=tz)
    if spec.interval_minutes:
        return IntervalTrigger(minutes=spec.interval_minutes, timezone=tz)
    return None


def _run_scheduled(name: str) -> None:
    """Scheduler entry point: never raises into APScheduler."""
    try:
        row = run_job(name)
        log.info("scheduled job finished", extra={"job": name, "status": row.status})
    except JobAlreadyRunning as exc:
        log.warning("scheduled job skipped", extra={"job": name, "reason": str(exc)})
    except Exception:
        log.exception("scheduled job crashed", extra={"job": name})


def start_scheduler(force: bool = False) -> bool:
    """Start the scheduler with every scheduled job in the registry.
    Returns True if running afterwards. No-op unless settings.scheduler_enabled (or force)."""
    global _scheduler
    if not (settings.scheduler_enabled or force):
        log.info("scheduler disabled (SCHEDULER_ENABLED=false)")
        return False
    import app.jobs  # noqa: F401  (make sure every job module is imported/registered)

    with _lock:
        if _scheduler is not None and _scheduler.running:
            return True
        tz = _timezone()
        sched = BackgroundScheduler(timezone=tz)
        for spec in JOBS.values():
            trigger = _trigger(spec, tz)
            if trigger is None:
                continue  # manual-only job
            sched.add_job(
                _run_scheduled, trigger, args=[spec.name], id=spec.name, name=spec.name,
                replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=300,
            )
        sched.start()
        _scheduler = sched
        log.info("scheduler started", extra={"jobs": len(sched.get_jobs()), "tz": tz})
        return True


def stop_scheduler(wait: bool = False) -> None:
    """Shut down the scheduler if running (idempotent)."""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            if _scheduler.running:
                _scheduler.shutdown(wait=wait)
            _scheduler = None
            log.info("scheduler stopped")


def scheduler_status() -> dict[str, Any]:
    """`{running, timezone, jobs: [{name, schedule, next_run}]}`; next_run is null when stopped."""
    import app.jobs  # noqa: F401

    running = _scheduler is not None and _scheduler.running
    scheduled = {j.id: j for j in _scheduler.get_jobs()} if running and _scheduler else {}
    jobs = []
    for spec in JOBS.values():
        aps = scheduled.get(spec.name)
        next_run = getattr(aps, "next_run_time", None) if aps else None
        jobs.append({"name": spec.name, "schedule": spec.schedule,
                     "next_run": next_run.isoformat() if next_run else None})
    return {"running": running, "timezone": _timezone(), "jobs": jobs}
