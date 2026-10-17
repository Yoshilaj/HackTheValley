import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.security import decrypt_str, encrypt_str
from app.db import models as m
from app.db.session import engine
from app.db.types import EMBEDDING_DIM, is_postgres


def _user(db) -> m.User:
    u = m.User(email=f"{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.flush()
    return u


def test_is_postgres_sqlite():
    from app.db import session

    assert is_postgres(session.engine) is False
    assert engine is not None


def test_vector_roundtrip_sqlite(db):
    src = m.ResearchSource(title="JSH 2019", year=2019, pathway_tags=["cardio"], kind="guideline")
    vec = [0.001 * i for i in range(EMBEDDING_DIM)]
    src.chunks.append(m.ResearchChunk(ordinal=0, text="Home BP >=135/85", embedding=vec, meta={"page": 3}))
    db.add(src)
    db.commit()
    db.expire_all()
    chunk = db.scalar(select(m.ResearchChunk).where(m.ResearchChunk.source_id == src.id))
    assert len(chunk.embedding) == EMBEDDING_DIM
    assert chunk.embedding[10] == pytest.approx(0.01)
    assert chunk.source.title == "JSH 2019"


def test_timeseries_roundtrip_and_utc(db):
    u = _user(db)
    t = datetime(2026, 10, 1, 7, 0, tzinfo=timezone(timedelta(hours=9)))  # JST
    db.add_all([
        m.MetricSample(user_id=u.id, type="resting_hr", value=62, unit="bpm", recorded_at=t, source="apple_health"),
        m.BpReading(user_id=u.id, session="morning", reading_no=1, systolic=146, diastolic=92, pulse=70,
                    measured_at=t, arm="left", position="seated", pre_medication=True),
        m.DeviceRhythmResult(user_id=u.id, source="omron_connect", label="possible_af",
                             raw_label="心房細動の可能性", recorded_at=t),
        m.DailyLog(user_id=u.id, date=date(2026, 10, 1), sleep_quality=3, flags={"tremor": False, "headache": True}),
        m.Observation(user_id=u.id, kind="symptom", label="fatigue", source="chat"),
        m.ScreenerResponse(user_id=u.id, screener="pd", answers={"tremor_rest": True}, score=1),
        m.AssessmentTask(user_id=u.id, task_type="sustained_vowel", protocol_version="v1", language="ja",
                         recording_quality={"snr_db": 25}, features={"jitter": 0.004},
                         model_scores={"pd_vowel": {"version": "v1", "probability": 0.3}}),
        m.UploadedDocument(user_id=u.id, kind="doctor_note", extracted={"dx": []}),
        m.ChatMessage(user_id=u.id, role="assistant", content="hi", sources=[{"id": "s1"}]),
        m.Notification(user_id=u.id, kind="level_change", title="t"),
        m.Job(name="assess_all_users", status="success", result={"n": 1}),
    ])
    db.commit()
    db.expire_all()
    bp = db.scalar(select(m.BpReading).where(m.BpReading.user_id == u.id))
    assert bp.measured_at.tzinfo is not None
    assert bp.measured_at == t and bp.measured_at.hour == 22  # stored/returned as UTC
    assert bp.valid is True
    drr = db.scalar(select(m.DeviceRhythmResult).where(m.DeviceRhythmResult.user_id == u.id))
    assert drr.raw_label == "心房細動の可能性"
    log = db.scalar(select(m.DailyLog).where(m.DailyLog.user_id == u.id))
    assert log.flags["headache"] is True and log.created_at.tzinfo is not None


def test_metric_sample_unique(db):
    u = _user(db)
    t = datetime(2026, 10, 2, tzinfo=timezone.utc)
    db.add(m.MetricSample(user_id=u.id, type="steps", value=1, recorded_at=t, source="shortcut"))
    db.commit()
    db.add(m.MetricSample(user_id=u.id, type="steps", value=2, recorded_at=t, source="shortcut"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_assessment_report_appointment(db):
    u = _user(db)
    ra = m.RiskAssessment(user_id=u.id, pathway="cardio", level="followup_recommended", priority_score=70,
                          fired_rules=[{"id": "BP-02"}, {"id": "RHY-01"}], missing_inputs=[], evidence=[])
    db.add(ra)
    db.flush()
    rep = m.Report(assessment_id=ra.id, user_id=u.id, markdown="# Report", share_token=uuid.uuid4().hex)
    apt = m.Appointment(user_id=u.id, assessment_id=ra.id, clinic={"name": "Clinic"}, status="requested",
                        proposed_slots=[{"start": "2026-10-20T10:00:00+09:00"}], agent_log=[])
    db.add_all([rep, apt])
    db.commit()
    db.expire_all()
    assert db.get(m.RiskAssessment, ra.id).reports[0].markdown == "# Report"
    assert db.get(m.Appointment, apt.id).updated_at is not None


def test_oauth_encryption(db):
    u = _user(db)
    cred = m.OAuthCredential(user_id=u.id, provider="google", encrypted_tokens=encrypt_str('{"refresh":"x"}'),
                             scopes=["calendar"])
    db.add(cred)
    db.commit()
    assert "refresh" not in cred.encrypted_tokens
    assert decrypt_str(cred.encrypted_tokens) == '{"refresh":"x"}'


def test_audit_helper(db):
    from app.core.security import audit

    u = _user(db)
    audit(db, u.id, "read", "bp_readings", {"n": 3}, commit=True)
    row = db.scalar(select(m.AuditLog).where(m.AuditLog.user_id == u.id))
    assert row.action == "read" and row.detail == {"n": 3}


def test_seed_idempotent(db):
    from scripts.seed import seed_demo_user

    a = seed_demo_user(db)
    b = seed_demo_user(db)
    assert a.id == b.id
    assert b.profile.age == 68 and b.profile.smoking_status == "current" and b.profile.known_af is False
    consents = db.scalars(select(m.Consent).where(m.Consent.user_id == a.id, m.Consent.revoked_at.is_(None))).all()
    assert {c.purpose for c in consents} == set(m.CONSENT_PURPOSES)
