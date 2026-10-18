"""ORM → app.ml.context mapping (app/services/context_builder.py; spec phase-a §8)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.models import (
    AssessmentTask, BpReading, DailyLog, DeviceRhythmResult, MetricSample, Observation, Profile, RiskAssessment,
    User,
)
from app.ml.context import PATHWAYS
from app.services.context_builder import (
    age_from_dob, build_contexts, ckd_stage_from, map_profile,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)  # 08:00 Toronto (EDT)


def make_user(db, **profile) -> User:
    u = User(email=f"cb-{uuid.uuid4().hex[:8]}@example.com")
    u.profile = Profile(**({"timezone": "America/Toronto"} | profile))
    db.add(u)
    db.commit()
    return u


def test_empty_user_is_none_safe(db):
    u = make_user(db)
    ctxs = build_contexts(db, u, as_of=NOW)
    assert list(ctxs) == list(PATHWAYS)
    c = ctxs["cardio"]
    assert c.bp_readings == [] and c.series == {} and c.prior_assessments == []
    assert c.profile.age is None and c.profile.ckd_stage is None and c.profile.smoker is None
    assert c.as_of.utcoffset() == timedelta(hours=-4)


def test_user_without_profile(db):
    u = User(email=f"np-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.commit()
    ctxs = build_contexts(db, u, as_of=NOW)
    assert ctxs["general"].profile.age is None


def test_profile_mapping():
    p = Profile(date_of_birth=date(1958, 1, 15), sex="male", smoking_status="current", diabetes=False, egfr=55.0,
                ldl_mg_dl=150, hdl_mg_dl=45, total_chol_mg_dl=225, known_af=None)
    pi = map_profile(p, date(2026, 10, 17))
    assert pi.age == 68 and pi.smoker is True and pi.ckd_stage == 3 and pi.ldl == 150 and pi.known_af is False
    assert map_profile(Profile(smoking_status="former"), date(2026, 1, 1)).smoker is False
    assert age_from_dob(None, date.today()) is None


@pytest.mark.parametrize("egfr,ckd,stage", [(95, None, 1), (70, None, 2), (40, None, 3), (20, None, 4),
                                             (10, None, 5), (None, True, 3), (None, False, 1), (None, None, None)])
def test_ckd_stage(egfr, ckd, stage):
    assert ckd_stage_from(egfr, ckd) == stage


def test_row_mapping_and_ids(db):
    u = make_user(db)
    t = NOW - timedelta(days=1)
    rows = [
        BpReading(user_id=u.id, session="morning", systolic=140, diastolic=90, pulse=70, measured_at=t, valid=False),
        DeviceRhythmResult(user_id=u.id, source="omron_connect", label="possible_af", raw_label="心房細動の可能性", recorded_at=t),
        Observation(user_id=u.id, kind="symptom", label="palpitations", severity="mild", created_at=t),
        AssessmentTask(user_id=u.id, task_type="sustained_vowel", valid=True, created_at=t,
                       model_scores={"pd_vowel": {"probability": 0.3, "threshold": 0.6, "version": "v1"}}),
        AssessmentTask(user_id=u.id, task_type="story_recall", score=6.5, created_at=t),
        RiskAssessment(user_id=u.id, pathway="cardio", level="watch", fired_rules=[{"id": "BP-01"}], created_at=t),
    ]
    db.add_all(rows)
    db.commit()
    c = build_contexts(db, u, as_of=NOW)["cardio"]
    bp = c.bp_readings[0]
    assert (bp.id, bp.systolic, bp.valid, bp.session) == (rows[0].id, 140, False, "morning")
    assert bp.measured_at.utcoffset() == timedelta(hours=-4)
    assert c.rhythm_results[0].label == "possible_af" and c.rhythm_results[0].id == rows[1].id
    assert c.observations[0].label == "palpitations" and c.observations[0].severity is None
    types = {t.task_type: t for t in c.task_sessions}
    assert types["sustained_vowel"].model_scores["pd_vowel"]["version"] == "v1"
    assert types["story_recall"].score == 6.5
    assert c.prior_assessments[0].level == "watch" and c.prior_assessments[0].fired_rules == [{"id": "BP-01"}]


def test_lookback_and_future_excluded(db):
    u = make_user(db)
    db.add_all([
        BpReading(user_id=u.id, session="morning", systolic=130, diastolic=80, measured_at=NOW + timedelta(hours=1)),
        BpReading(user_id=u.id, session="morning", systolic=130, diastolic=80, measured_at=NOW - timedelta(days=200)),
    ])
    db.commit()
    assert build_contexts(db, u, as_of=NOW)["cardio"].bp_readings == []


def test_series_derivation(db):
    u = make_user(db)
    d1 = NOW - timedelta(days=1)
    db.add_all([
        MetricSample(user_id=u.id, type="walking_speed_mps", value=1.0, recorded_at=d1, source="apple_health"),
        MetricSample(user_id=u.id, type="walking_speed_mps", value=1.2, recorded_at=d1 + timedelta(minutes=5), source="apple_health"),
        MetricSample(user_id=u.id, type="step_length_m", value=0.7, recorded_at=d1, source="apple_health"),
        MetricSample(user_id=u.id, type="double_support_pct", value=28, recorded_at=d1, source="apple_health"),
        MetricSample(user_id=u.id, type="walking_asymmetry_pct", value=3, recorded_at=d1, source="apple_health"),
        MetricSample(user_id=u.id, type="sleep_asleep_minutes", value=300, recorded_at=d1, source="apple_health"),
        MetricSample(user_id=u.id, type="sleep_asleep_minutes", value=60, recorded_at=d1 + timedelta(minutes=1), source="apple_health"),
        MetricSample(user_id=u.id, type="sleep_asleep_minutes", value=200, recorded_at=d1, source="fitbit"),
        MetricSample(user_id=u.id, type="steps", value=5000, recorded_at=d1, source="apple_health"),
        DailyLog(user_id=u.id, date=d1.date(), fatigue=3),
        DailyLog(user_id=u.id, date=(d1 - timedelta(days=1)).date(), fatigue=None),
        AssessmentTask(user_id=u.id, task_type="finger_tap", created_at=d1,
                       features={"tap_count": 40, "mean_iti_ms": 250.0, "iti_cv": 0.1}),
        AssessmentTask(user_id=u.id, task_type="finger_tap", created_at=d1 - timedelta(days=2),
                       features={"left": {"tap_count": 30, "mean_iti_ms": 300.0, "iti_cv": 0.2},
                                 "right": {"tap_count": 40, "mean_iti_ms": 200.0, "iti_cv": None}}),
        AssessmentTask(user_id=u.id, task_type="finger_tap", created_at=d1, valid=False, features={"tap_count": 1}),
    ])
    db.commit()
    s = build_contexts(db, u, as_of=NOW)["parkinsons"].series
    local_day = (d1 - timedelta(hours=4)).date()  # America/Toronto (EDT)
    assert s["walking_speed"].points == [(local_day, pytest.approx(1.1))]
    assert s["step_length"].points[0][1] == 0.7
    assert "double_support_pct" in s and "walking_asymmetry_pct" in s
    assert s["sleep_duration"].points == [(local_day, 6.0)]  # 360 min apple_health (max source) / 60
    assert s["fatigue"].points == [(d1.date(), 3.0)]
    assert "steps" not in s
    assert s["tap_count"].points[-1] == (local_day, 40.0)
    assert s["tap_count"].points[0][1] == 35.0  # per-hand average
    assert s["tap_interval_mean"].points[0][1] == 250.0
    assert s["tap_interval_cv"].points[0][1] == 0.2  # only valid hand value


def test_vowel_repetitions_collapse_to_one_session_per_day(db):
    """Three repetitions on one day + one on another → two PD-03 sessions, median probability."""
    from app.services.context_builder import aggregate_vowel_sessions  # noqa: F401  (public helper)

    u = make_user(db)
    day1 = NOW - timedelta(days=9)
    rows = [AssessmentTask(user_id=u.id, task_type="sustained_vowel", valid=True, created_at=day1 + timedelta(minutes=i),
                           model_scores={"pd_vowel": {"probability": p, "threshold": 0.6, "version": "v1"}})
            for i, p in enumerate((0.2, 0.9, 0.7))]
    rows.append(AssessmentTask(user_id=u.id, task_type="sustained_vowel", valid=True, created_at=NOW - timedelta(days=1),
                               model_scores={"pd_vowel": {"probability": 0.65, "threshold": 0.6, "version": "v1"}}))
    rows.append(AssessmentTask(user_id=u.id, task_type="sustained_vowel", valid=False, created_at=NOW - timedelta(days=1)))
    db.add_all(rows)
    db.commit()
    vowels = [t for t in build_contexts(db, u, as_of=NOW)["parkinsons"].task_sessions if t.task_type == "sustained_vowel"]
    scored = [t for t in vowels if t.valid]
    assert len(scored) == 2 and len(vowels) == 3
    first = scored[0].model_scores["pd_vowel"]
    assert first["probability"] == 0.7 and first["repetitions"] == 3 and scored[0].id == rows[2].id
    assert set(first["task_ids"]) == {r.id for r in rows[:3]}


def test_task_features_device_and_quality_mapped(db):
    """PD-03B inputs (spec §5.5): features / device / recording_quality reach TaskSessionIn; vowel repetitions
    on one day merge their features by per-key median; device comes from recording_quality or raw_json."""
    from app.services.context_builder import task_device

    u = make_user(db)
    day = NOW - timedelta(days=3)
    rows = [
        AssessmentTask(user_id=u.id, task_type="sustained_vowel", valid=True, created_at=day + timedelta(minutes=i),
                       features={"MDVP:Shimmer": v, "NHR": 0.01 + i / 1000, "CPPS": 14.0, "note": "x"},
                       recording_quality={"snr_db": 25, "noise_floor_dbfs": -60.0 - i, "device": "iPhone"})
        for i, v in enumerate((0.03, 0.05, 0.04))
    ]
    rows.append(AssessmentTask(user_id=u.id, task_type="story_recall", valid=True, score=6.0, created_at=NOW - timedelta(days=1),
                               raw_json={"device": "Mac", "story_ids": []},
                               features={"temporal": {"immediate_1": {"speech_rate_wpm": 120.0, "pause_ratio": 0.2,
                                                                      "timing_source": "word_timestamps", "speech_span_s": 30}}},
                               recording_quality={"immediate_1": {"audio": {"noise_floor_dbfs": -55.0}}}))
    rows.append(AssessmentTask(user_id=u.id, task_type="sustained_vowel", valid=True, created_at=NOW - timedelta(days=2)))
    db.add_all(rows)
    db.commit()
    tasks = build_contexts(db, u, as_of=NOW)["parkinsons"].task_sessions
    vowels = [t for t in tasks if t.task_type == "sustained_vowel"]
    assert len(vowels) == 2  # three repetitions collapsed (no model score needed), bare row kept
    merged = next(t for t in vowels if t.features)
    assert merged.id == rows[2].id and merged.features["MDVP:Shimmer"] == 0.04 and merged.features["NHR"] == 0.011
    assert merged.features["note"] == "x" and merged.device == "iPhone" and merged.recording_quality["noise_floor_dbfs"] == -62.0
    assert merged.model_scores == {} and merged.score is None
    story = next(t for t in tasks if t.task_type == "story_recall")
    assert story.device == "Mac" and story.features["temporal"]["immediate_1"]["speech_rate_wpm"] == 120.0
    assert story.recording_quality["immediate_1"]["audio"]["noise_floor_dbfs"] == -55.0
    bare = next(t for t in vowels if not t.features)
    assert bare.device is None and bare.recording_quality == {}
    assert task_device(AssessmentTask(recording_quality={"device": " "}, raw_json={"device": "iPad"})) == "iPad"
    assert task_device(AssessmentTask()) is None
