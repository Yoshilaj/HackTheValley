"""assess_user: persistence + transitions (app/services/risk_service.py; spec phase-a §8, §10, §11)."""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    AuditLog, BpReading, Consent, DeviceRhythmResult, Notification, Profile, RiskAssessment, User,
)
from app.jobs import run_job
from app.services import risk_service as rs
from app.services.preventive import preventive_measures
from app.ml.context import ProfileIn

TZ_OFFSET = timedelta(hours=-4)  # America/Toronto (EDT) around NOW


def make_user(db, consents=("data_processing", "ai_assessment")) -> User:
    u = User(email=f"rs-{uuid.uuid4().hex[:8]}@example.com")
    u.profile = Profile(language="en", timezone="America/Toronto", date_of_birth=date(1958, 1, 15), sex="male",
                        smoking_status="never", diabetes=False, egfr=70, ldl_mg_dl=120, hdl_mg_dl=55)
    db.add(u)
    db.flush()
    for p in consents:
        db.add(Consent(user_id=u.id, purpose=p))
    db.commit()
    return u


def add_bp(db, user, now, sys_, dia, days=7):
    today = (now + TZ_OFFSET).date()
    for i in range(days):
        for session, hh in (("morning", 7), ("evening", 20)):
            for k in range(2):
                t = datetime.combine(today - timedelta(days=i), time(hh, k), tzinfo=timezone(TZ_OFFSET))
                if t <= now:
                    db.add(BpReading(user_id=user.id, session=session, systolic=sys_, diastolic=dia, measured_at=t))
    db.commit()


def kinds(db, user):
    return [n.kind for n in db.scalars(select(Notification).where(Notification.user_id == user.id)
                                       .order_by(Notification.created_at))]


@pytest.fixture()
def hooks():
    calls = []
    rs.FOLLOWUP_HOOKS.append(lambda db, user, a: calls.append((user.id, a.pathway, a.level)))
    yield calls
    rs.FOLLOWUP_HOOKS.clear()


NOW = datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc)


def test_rows_persisted_with_contract_fields(db):
    u = make_user(db)
    add_bp(db, u, NOW, 150, 95)
    rows = rs.assess_user(db, u, as_of=NOW)
    assert [r.pathway for r in rows] == ["cognitive", "cardio", "parkinsons", "general"]
    cardio = rows[1]
    assert cardio.level == "followup_recommended" and cardio.priority_score == 60
    f = cardio.fired_rules[0]
    assert set(f) >= {"id", "version", "level", "summary_text", "evidence", "sources"} and f["id"] == "BP-02"
    assert cardio.evidence and cardio.evidence[0]["rule_id"] == "BP-02"
    assert cardio.model_versions["rules"]["BP-02"] == "1"
    assert cardio.probabilities[0]["source_id"] == "src_framingham2008"
    assert cardio.probabilities[0]["missing_inputs"] == ["bp_treated", "total_chol"]
    assert 2 <= len(cardio.preventive_measures) <= 4
    assert all(m["source_id"] for m in cardio.preventive_measures)
    assert "BP-02" in cardio.explanation and "not a diagnosis" in cardio.explanation
    assert cardio.data_quality["valid_bp_days"] == 7
    assert rows[0].level == "insufficient_data" and rows[0].priority_score is None
    assert db.scalar(select(AuditLog).where(AuditLog.user_id == u.id, AuditLog.action == "risk.assess"))
    assert rs.latest_assessments(db, u.id)["cardio"].id == cardio.id


def test_followup_notifies_and_calls_hooks_once(db, hooks):
    u = make_user(db)
    add_bp(db, u, NOW, 150, 95)
    rs.assess_user(db, u, as_of=NOW)
    k = kinds(db, u)
    assert k.count("followup_recommended") == 1
    assert "risk_level_change" not in k  # no previous assessment
    assert hooks == [(u.id, "cardio", "followup_recommended")]
    fu = db.scalar(select(Notification).where(Notification.user_id == u.id, Notification.kind == "followup_recommended"))
    assert fu.payload["email"]["sent"] in (True, False) and fu.payload["pathway"] == "cardio"
    # Second run: still followup → no new followup notification, no hook call
    rs.assess_user(db, u, as_of=NOW + timedelta(hours=1))
    assert kinds(db, u).count("followup_recommended") == 1
    assert len(hooks) == 1


def test_no_hook_without_ai_consent(db, hooks):
    u = make_user(db, consents=("data_processing",))
    add_bp(db, u, NOW, 150, 95)
    rs.assess_user(db, u, as_of=NOW)
    assert "followup_recommended" in kinds(db, u)
    assert hooks == []


def test_level_change_notification(db, hooks):
    u = make_user(db)
    add_bp(db, u, NOW - timedelta(days=10), 120, 75)  # old normal readings
    db.add(RiskAssessment(user_id=u.id, pathway="cardio", level="no_finding", priority_score=0, fired_rules=[],
                          created_at=NOW - timedelta(days=1)))
    db.commit()
    add_bp(db, u, NOW, 150, 95)
    rs.assess_user(db, u, as_of=NOW)
    k = kinds(db, u)
    assert "risk_level_change" in k and "followup_recommended" in k
    change = db.scalar(select(Notification).where(Notification.user_id == u.id, Notification.kind == "risk_level_change"))
    assert change.payload["previous_level"] == "no_finding" and change.payload["level"] == "followup_recommended"


def test_data_needed_throttled(db):
    u = make_user(db)
    rs.assess_user(db, u, as_of=NOW)
    first = kinds(db, u).count("data_needed")
    assert first >= 1  # cognitive / cardio / parkinsons lack data
    rs.assess_user(db, u, as_of=NOW + timedelta(days=1))
    assert kinds(db, u).count("data_needed") == first
    # After 7 days the reminder is sent again (notification created_at is wall-clock now → back-date them).
    for n in db.scalars(select(Notification).where(Notification.user_id == u.id)):
        n.created_at = n.created_at - timedelta(days=8)
    db.commit()
    rs.assess_user(db, u, as_of=NOW + timedelta(days=9))
    assert kinds(db, u).count("data_needed") == 2 * first


def test_hook_errors_are_swallowed(db):
    def boom(db, user, a):
        raise RuntimeError("agent down")
    rs.FOLLOWUP_HOOKS.append(boom)
    try:
        u = make_user(db)
        add_bp(db, u, NOW, 150, 95)
        rows = rs.assess_user(db, u, as_of=NOW)
        assert rows[1].level == "followup_recommended"
        assert db.scalar(select(AuditLog).where(AuditLog.user_id == u.id, AuditLog.action == "risk.followup_hook_error"))
    finally:
        rs.FOLLOWUP_HOOKS.clear()


def test_rhythm_plus_bp_scores_70(db):
    u = make_user(db)
    add_bp(db, u, NOW, 150, 95)
    db.add(DeviceRhythmResult(user_id=u.id, source="omron_connect", label="possible_af", recorded_at=NOW - timedelta(days=2)))
    db.commit()
    cardio = rs.assess_user(db, u, as_of=NOW)[1]
    assert sorted(f["id"] for f in cardio.fired_rules) == ["BP-02", "RHY-01"]
    assert cardio.priority_score == 70


def test_history_weekly(db):
    u = make_user(db)
    for w in range(3):
        rs.assess_user(db, u, as_of=datetime.now(timezone.utc) - timedelta(weeks=w))
    hist = rs.assessment_history(db, u.id, weeks=8)
    cardio = [h for h in hist if h["pathway"] == "cardio"]
    assert len(cardio) == 3 and cardio == sorted(cardio, key=lambda h: h["week"])


def test_invalid_trigger(db):
    with pytest.raises(ValueError):
        rs.assess_user(db, make_user(db), trigger="bogus")


def test_explain_with_llm_grounded(db):
    u = make_user(db)
    add_bp(db, u, NOW, 150, 95)
    cardio = rs.assess_user(db, u, as_of=NOW)[1]

    class FakeLLM:
        prompt = None

        def complete(self, prompt):
            FakeLLM.prompt = prompt
            return "explained"
    assert rs.explain_with_llm(cardio, FakeLLM()) == "explained"
    assert "BP-02" in FakeLLM.prompt and "not a diagnosis" in FakeLLM.prompt

    class Broken:
        def complete(self, prompt):
            raise RuntimeError
    assert rs.explain_with_llm(cardio, Broken()) == cardio.explanation


def test_preventive_measures_rules():
    bp = preventive_measures("cardio", ["BP-02"], ProfileIn(smoker=True, ldl=160), [])
    assert 2 <= len(bp) <= 4 and bp[0]["source_id"] == "src_hypertension_canada_2020"
    assert any(m["source_id"] == "src_ccs_lipids_2021" and m["related_rule"] == "FRS-01" for m in bp)
    jp = preventive_measures("cardio", ["BP-02"], ProfileIn(smoker=True, ldl=160),
                             [{"source_id": "src_suita2014", "missing_inputs": []}])
    assert any(m["source_id"] == "src_suita2014" for m in jp)
    assert all({"text_en", "text_ja", "source_id", "related_rule"} <= set(m) for m in bp)
    cog = preventive_measures("cognitive", [], None, [])
    assert len(cog) == 4 and all("not a way to lower a disease percentage" in m["text_en"] for m in cog)
    assert preventive_measures("parkinsons", ["PD-04"], None)[0]["related_rule"] == "PD-04"
    assert len(preventive_measures("general", [], None)) == 2
    assert preventive_measures("cardio", [], ProfileIn(), [])  # fallback ≥2
    assert len(preventive_measures("cardio", [], ProfileIn(), [])) >= 2
    assert preventive_measures("cardio", ["BP-02"], None) == preventive_measures("cardio", ["BP-02"], None)


def test_nightly_job(db):
    u = make_user(db)
    job = run_job("nightly_risk_assessment")
    assert job.status == "done", job.error
    assert job.result["users"] >= 1 and job.result["failed"] == 0
    db.expire_all()
    assert rs.latest_assessments(db, u.id)
