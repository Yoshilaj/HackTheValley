"""Adaptive daily check-in (deterministic)."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.auth import create_access_token
from app.db.models import (
    AssessmentTask, BpReading, DailyLog, MetricSample, Notification, Observation, Profile, RiskAssessment,
    ScreenerResponse, User,
)
from app.services.checkin_service import daily_checkin_questions, submit_checkin

# 2026-10-17 08:00 America/Toronto (EDT, UTC-4)
MORNING = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
EVENING = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)  # 20:00 Toronto


@pytest.fixture()
def user(db):
    u = User(email=f"checkin-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.flush()
    db.add(Profile(user_id=u.id, language="en", timezone="America/Toronto"))
    db.commit()
    return u


def ids(qs):
    return [q["id"] for q in qs]


def test_missing_data_questions(db, user):
    qs = daily_checkin_questions(db, user, "en", now=MORNING)
    assert ids(qs) == ["sleep_quality", "bp_reminder", "fatigue", "mood", "task_story_recall"]
    bp = qs[1]
    assert bp["type"] == "bool" and bp["sessions"] == ["morning", "evening"]
    assert "within 1 hour of waking" in bp["text"] and "before bed" in bp["text"]
    assert all(q["type"] in ("scale", "bool", "text") and q["reason"] for q in qs)
    ja = daily_checkin_questions(db, user, "ja", now=MORNING)
    assert "起床後1時間以内" in ja[1]["text"]


def test_present_data_suppresses_questions(db, user):
    db.add(MetricSample(user_id=user.id, type="sleep_asleep_minutes", value=420, recorded_at=MORNING - timedelta(hours=2)))
    db.add(BpReading(user_id=user.id, session="morning", systolic=130, diastolic=80, measured_at=MORNING))
    db.add(DailyLog(user_id=user.id, date=date(2026, 10, 17), fatigue=2, mood=4))
    db.add(AssessmentTask(user_id=user.id, task_type="story_recall", created_at=MORNING - timedelta(days=2)))
    db.commit()
    assert daily_checkin_questions(db, user, "en", now=MORNING) == []
    # in the evening, the missing evening session is requested
    qs = daily_checkin_questions(db, user, "en", now=EVENING)
    assert ids(qs) == ["bp_reminder"] and qs[0]["sessions"] == ["evening"]


def test_watch_pathways_add_targeted_questions(db, user):
    db.add(RiskAssessment(user_id=user.id, pathway="cardio", level="watch", fired_rules=[{"id": "BP-01"}]))
    db.add(RiskAssessment(user_id=user.id, pathway="cognitive", level="followup_recommended"))
    db.add(RiskAssessment(user_id=user.id, pathway="parkinsons", level="no_finding"))
    db.commit()
    qs = daily_checkin_questions(db, user, "en", now=MORNING)
    got = ids(qs)
    for q in ("palpitations", "dizziness", "breathlessness", "memory_lapse", "functional_change"):
        assert q in got
    assert "tremor" not in got
    assert next(q for q in qs if q["id"] == "functional_change")["type"] == "text"
    assert next(q for q in qs if q["id"] == "palpitations")["pathway"] == "cardio"


def test_pd_screener_positive_prompts_vowel_task(db, user):
    db.add(ScreenerResponse(user_id=user.id, screener="pd", answers={"pd_rest_tremor": True, "pd_soft_voice": True}))
    db.commit()
    qs = daily_checkin_questions(db, user, "en", now=MORNING)
    tasks = [q for q in qs if q["id"].startswith("task_")]
    assert [t["id"] for t in tasks] == ["task_sustained_vowel"] and tasks[0]["task"] == "sustained_vowel"


def test_submit_checkin_upserts_log_and_observations(db, user):
    res = submit_checkin(db, user, {"sleep_quality": 2, "fatigue": "4", "palpitations": True, "dizziness": "no",
                                    "functional_change": "I forgot to pay a bill", "bp_reminder": True, "bogus": 1},
                         on_date=date(2026, 10, 17))
    log = res["daily_log"]
    assert (log.sleep_quality, log.fatigue) == (2, 4)
    assert log.flags == {"palpitations": True, "dizziness": False}
    assert {(o.kind, o.label) for o in res["observations"]} == {
        ("symptom", "palpitations"), ("functional_change", "functional_change_reported")}
    assert res["ignored"] == ["bogus"] and res["red_flags"] == []
    # resubmission merges flags and stays idempotent for the same flag
    res2 = submit_checkin(db, user, {"mood": 3, "tremor": True}, on_date=date(2026, 10, 17))
    assert res2["daily_log"].id == log.id and res2["daily_log"].fatigue == 4
    assert res2["daily_log"].flags == {"palpitations": True, "dizziness": False, "tremor": True}
    labels = db.scalars(select(Observation.label).where(Observation.user_id == user.id, Observation.source == "log"))
    # review fix 8: only observations whose flag became false are deleted; the check-in's
    # functional_change_reported (not flag-derived) is kept instead of being wiped
    assert sorted(labels) == ["functional_change_reported", "palpitations", "tremor"]


def test_submit_checkin_red_flag_text(db, user):
    res = submit_checkin(db, user, {"functional_change": "Yesterday I fainted at the station"},
                         on_date=date(2026, 10, 17))
    assert res["red_flags"] == ["fainting"] and "EMERGENCY" in res["emergency_guidance"]
    assert ("red_flag", "fainting") in {(o.kind, o.label) for o in res["observations"]}
    kinds = db.scalars(select(Notification.kind).where(Notification.user_id == user.id)).all()
    # exactly one emergency notification; the event assessment (review fix 3) may add the COG-03
    # follow-up notification for the reported functional change (hooks stay suppressed, §7)
    assert kinds.count("emergency") == 1 and set(kinds) <= {"emergency", "followup_recommended"}


def test_checkin_routes(client, user):
    h = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    qs = client.get("/api/chat/checkin?language=ja", headers=h)
    assert qs.status_code == 200 and {"id", "text", "type", "reason"} <= set(qs.json()[0])
    res = client.post("/api/chat/checkin", headers=h, json={"answers": {"fatigue": 3, "mood": 4}})
    assert res.status_code == 200, res.text
    assert res.json()["daily_log_id"] and res.json()["red_flags"] == []
