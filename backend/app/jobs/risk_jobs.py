"""Scheduled risk jobs (BACKEND_PLAN C5): nightly re-assessment and the Monday weekly summary.

Both iterate over every user and isolate failures: one failing user is counted in ``failed``
and never stops the rest.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AssessmentTask, BpReading, DailyLog, RiskAssessment, User
from app.jobs.registry import HOOK_NIGHTLY_RISK_ASSESSMENT, job

log = get_logger(__name__)

HOOK_WEEKLY_SUMMARY = "weekly_summary_notifications"
WEEK = timedelta(days=7)

_LEVEL_TEXT = {
    "insufficient_data": {"en": "not enough data", "ja": "データ不足"},
    "invalid_measurement": {"en": "measurement not usable", "ja": "測定が無効"},
    "no_finding": {"en": "no finding", "ja": "所見なし"},
    "watch": {"en": "watch", "ja": "経過観察"},
    "followup_recommended": {"en": "follow-up recommended", "ja": "受診をおすすめ"},
}
_CHANGE_TEXT = {
    "up": {"en": "up vs last week", "ja": "先週より上昇"},
    "down": {"en": "down vs last week", "ja": "先週より低下"},
    "same": {"en": "same as last week", "ja": "先週と同じ"},
    "new": {"en": "first assessment", "ja": "初回の判定"},
}


@job(HOOK_NIGHTLY_RISK_ASSESSMENT, cron="0 3 * * *")
def nightly_risk_assessment(db: Session) -> dict[str, Any]:
    """Run ``assess_user(trigger='nightly')`` for every user; one failing user never stops the rest."""
    from app.services.risk_service import assess_user

    counts: dict[str, Any] = {"users": 0, "assessed": 0, "failed": 0, "levels": {}}
    for user_id in list(db.scalars(select(User.id))):
        counts["users"] += 1
        try:
            user = db.get(User, user_id)
            for row in assess_user(db, user, trigger="nightly"):
                counts["levels"][row.level] = counts["levels"].get(row.level, 0) + 1
            counts["assessed"] += 1
        except Exception as exc:
            db.rollback()
            counts["failed"] += 1
            log.exception("nightly assessment failed", extra={"user_id": user_id, "error": str(exc)})
    return counts


# ---------------------------------------------------------------- weekly summary

def _change(row: RiskAssessment, prev: RiskAssessment | None) -> str:
    """``up | down | same | new``: priority score vs the assessment of a week ago, level as fallback."""
    if prev is None:
        return "new"
    if row.priority_score is not None and prev.priority_score is not None:
        if row.priority_score > prev.priority_score:
            return "up"
        return "down" if row.priority_score < prev.priority_score else "same"
    return "same" if row.level == prev.level else "up"


def _week_activity(db: Session, user: User, since: datetime, now: datetime) -> dict[str, Any]:
    """Last-7-day activity: tasks by type, distinct local days with a valid BP reading, daily logs."""
    from app.services.context_builder import to_local, user_zone

    tz = user_zone(user.profile)
    tasks: dict[str, int] = {}
    for t_type in db.scalars(select(AssessmentTask.task_type).where(
            AssessmentTask.user_id == user.id, AssessmentTask.created_at >= since, AssessmentTask.created_at <= now)):
        tasks[t_type] = tasks.get(t_type, 0) + 1
    bp_days = {to_local(m, tz).date() for m in db.scalars(select(BpReading.measured_at).where(
        BpReading.user_id == user.id, BpReading.valid.is_(True), BpReading.measured_at >= since,
        BpReading.measured_at <= now))}
    since_day: date = to_local(since, tz).date()
    checkins = len(list(db.scalars(select(DailyLog.id).where(
        DailyLog.user_id == user.id, DailyLog.date >= since_day, DailyLog.date <= to_local(now, tz).date()))))
    return {"tasks": tasks, "bp_days": len(bp_days), "checkins": checkins}


def _weekly_text(lang: str, lines: list[str], activity: dict[str, Any]) -> tuple[str, str]:
    t = activity["tasks"]
    if lang == "ja":
        title = "今週のまとめ"
        week_line = (f"今週: 記録 {activity['checkins']} 回、血圧 {activity['bp_days']} 日、タッピング {t.get('finger_tap', 0)} 回、"
                     f"声 {t.get('sustained_vowel', 0)} 回、物語の記憶 {t.get('story_recall', 0)} 回")
    else:
        title = "Your weekly summary"
        week_line = (f"This week: {activity['checkins']} check-ins, {activity['bp_days']} BP days, "
                     f"{t.get('finger_tap', 0)} tapping, {t.get('sustained_vowel', 0)} voice, "
                     f"{t.get('story_recall', 0)} story tasks")
    return title, "\n".join([*lines, week_line])


def _weekly_summary_for_user(db: Session, user: User, now: datetime) -> None:
    """Re-assess (trigger ``weekly``), compare with a week ago and write one ``weekly_summary`` notification."""
    from app.services.context_builder import to_local, user_zone
    from app.services.notify import KIND_WEEKLY_SUMMARY, notify
    from app.services.risk_service import PATHWAY_NAMES, _lang, assess_user, previous_assessment

    lang = _lang(user)
    rows = assess_user(db, user, as_of=now, trigger="weekly", commit=False)
    levels: dict[str, str] = {}
    previous_levels: dict[str, str | None] = {}
    scores: dict[str, int | None] = {}
    lines: list[str] = []
    for row in rows:
        prev = previous_assessment(db, user.id, row.pathway, before=now - WEEK)
        levels[row.pathway] = row.level
        previous_levels[row.pathway] = prev.level if prev else None
        scores[row.pathway] = row.priority_score
        name = PATHWAY_NAMES.get(row.pathway, {}).get(lang, row.pathway)
        level = _LEVEL_TEXT.get(row.level, {}).get(lang, row.level)
        score = "–" if row.priority_score is None else str(row.priority_score)
        lines.append(f"{name}: {level} ({score}/100, {_CHANGE_TEXT[_change(row, prev)][lang]})")
    activity = _week_activity(db, user, now - WEEK, now)
    title, body = _weekly_text(lang, lines, activity)
    local_today = to_local(now, user_zone(user.profile)).date()
    monday = local_today - timedelta(days=local_today.weekday())
    notify(db, user.id, KIND_WEEKLY_SUMMARY, title, body, commit=False,
           payload={"week": monday.isoformat(), "levels": levels, "previous_levels": previous_levels,
                    "scores": scores, **activity})


@job(HOOK_WEEKLY_SUMMARY, cron="0 9 * * 1")
def weekly_summary_notifications(db: Session) -> dict[str, Any]:
    """Monday job: per user, ``assess_user(trigger='weekly')`` + one in-app ``weekly_summary`` notification
    comparing each pathway with the assessment of a week earlier and counting last week's activity."""
    now = datetime.now(timezone.utc)
    counts: dict[str, Any] = {"notified": 0, "assessed": 0, "failed": 0}
    for user_id in list(db.scalars(select(User.id))):
        try:
            user = db.get(User, user_id)
            _weekly_summary_for_user(db, user, now)
            db.commit()  # one transaction per user, so a later failure's rollback keeps this one
            counts["assessed"] += 1
            counts["notified"] += 1
        except Exception as exc:
            db.rollback()
            counts["failed"] += 1
            log.exception("weekly summary failed", extra={"user_id": user_id, "error": str(exc)})
    return counts
