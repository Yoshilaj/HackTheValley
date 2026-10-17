from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Job, Notification
from app.jobs import JOBS, JobAlreadyRunning, UnknownJob, job, run_job
from app.jobs import scheduler as sched


@pytest.fixture()
def temp_jobs():
    """Register throwaway jobs; remove them afterwards."""
    added: list[str] = []

    def register(name, fn, **kw):
        job(name, **kw)(fn)
        added.append(name)

    yield register
    for name in added:
        JOBS.pop(name, None)


def test_builtin_jobs_registered():
    assert {"cleanup_expired_jobs", "weekly_summary_notifications"} <= set(JOBS)


def test_job_decorator_validation():
    with pytest.raises(ValueError):
        job("bad", interval_minutes=5, cron="* * * * *")


def test_run_job_success(db, temp_jobs):
    temp_jobs("t_ok", lambda s: {"n": 3}, interval_minutes=10)
    row = run_job("t_ok")
    assert row.status == "done" and row.result == {"n": 3} and row.error is None
    assert row.started_at and row.finished_at and row.scheduled_for
    assert db.get(Job, row.id).status == "done"


def test_run_job_failure_recorded(db, temp_jobs):
    def bad(s):
        raise ValueError("kaput")

    temp_jobs("t_fail", bad)
    row = run_job("t_fail", db=db)
    assert row.status == "failed" and "kaput" in row.error and row.finished_at


def test_run_job_unknown():
    with pytest.raises(UnknownJob):
        run_job("does_not_exist")


def test_idempotency_guard(db, temp_jobs):
    temp_jobs("t_guard", lambda s: {})
    db.add(Job(name="t_guard", status="running", started_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
    db.commit()
    with pytest.raises(JobAlreadyRunning):
        run_job("t_guard")
    # a stale 'running' row (> 1 h) does not block
    db.add(Job(name="t_guard", status="running", started_at=datetime.now(timezone.utc) - timedelta(hours=2),
               created_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
    db.commit()
    assert run_job("t_guard").status == "done"


def test_builtin_jobs_run(db, demo_user):
    assert run_job("cleanup_expired_jobs").status == "done"
    row = run_job("weekly_summary_notifications")
    assert row.status == "done" and row.result["notified"] >= 1
    started = row.started_at
    assert db.scalar(
        select(Notification).where(
            Notification.user_id == demo_user.id, Notification.kind == "weekly_summary",
            Notification.created_at >= started,
        )
    ) is not None


def test_scheduler_disabled_noop(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    try:
        assert sched.start_scheduler() is False
        assert sched.scheduler_status()["running"] is False
    finally:
        sched.stop_scheduler()


def test_scheduler_start_stop(monkeypatch, temp_jobs):
    temp_jobs("t_sched", lambda s: {}, interval_minutes=60)
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    try:
        assert sched.start_scheduler() is True
        status = sched.scheduler_status()
        assert status["running"] is True and status["timezone"] == settings.scheduler_timezone
        by_name = {j["name"]: j for j in status["jobs"]}
        assert by_name["t_sched"]["next_run"] is not None
        assert by_name["cleanup_expired_jobs"]["next_run"] is not None
        assert sched.start_scheduler() is True  # idempotent
    finally:
        sched.stop_scheduler()
    assert sched.scheduler_status()["running"] is False


# ---------------------------------------------------------------- admin routes

def test_job_routes_require_admin(client, auth_headers):
    assert client.get("/api/jobs").status_code == 403
    assert client.get("/api/jobs", headers=auth_headers).status_code == 403
    assert client.post("/api/jobs/cleanup_expired_jobs/run", headers={"X-Admin-Secret": "wrong"}).status_code == 403
    assert client.get("/api/jobs/scheduler").status_code == 403


def test_job_routes(client, admin_headers, temp_jobs):
    temp_jobs("t_route", lambda s: {"ok": True})
    r = client.post("/api/jobs/t_route/run", headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["result"] == {"ok": True}
    r = client.get("/api/jobs?name=t_route", headers=admin_headers)
    assert r.status_code == 200 and r.json()[0]["name"] == "t_route"
    assert client.post("/api/jobs/nope/run", headers=admin_headers).status_code == 404
    r = client.get("/api/jobs/scheduler", headers=admin_headers)
    assert r.status_code == 200 and r.json()["running"] is False
    assert any(j["name"] == "t_route" for j in r.json()["jobs"])
