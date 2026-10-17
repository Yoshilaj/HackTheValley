"""Demo replay (spec phase-a §8): planted demo user → cardio [BP-02, RHY-01] → followup_recommended."""
import uuid

from sqlalchemy import func, select

from app.db.models import BpReading, Consent, Profile, User
from app.services.risk_service import assess_user
from scripts.seed import DEMO_PROFILE, _dob_for_age
from scripts.seed_demo import bp_for_offset, seed_planted_trend


def make_demo_like(db) -> User:
    u = User(email=f"sd-{uuid.uuid4().hex[:8]}@example.com")
    u.profile = Profile(**(DEMO_PROFILE | {"date_of_birth": _dob_for_age(68)}))
    db.add(u)
    db.flush()
    db.add(Consent(user_id=u.id, purpose="ai_assessment"))
    db.commit()
    return u


def test_bp_curve():
    assert bp_for_offset(20) == (128, 80) and bp_for_offset(0) == (149, 94)
    assert bp_for_offset(7)[0] < bp_for_offset(3)[0]


def test_seed_is_idempotent(db):
    u = make_demo_like(db)
    first = seed_planted_trend(db, u)
    n = db.scalar(select(func.count(BpReading.id)).where(BpReading.user_id == u.id))
    assert first["skipped"] is False and n > 100
    assert seed_planted_trend(db, u) == {"skipped": True}
    assert db.scalar(select(func.count(BpReading.id)).where(BpReading.user_id == u.id)) == n


def test_demo_replay(db):
    u = make_demo_like(db)
    seed_planted_trend(db, u)
    rows = {r.pathway: r for r in assess_user(db, u)}
    cardio = rows["cardio"]
    fired = {f["id"] for f in cardio.fired_rules}
    assert cardio.level == "followup_recommended"
    assert {"BP-02", "RHY-01"} <= fired
    assert cardio.priority_score >= 70
    assert rows["general"].level != "emergency" and rows["parkinsons"].level not in ("watch", "followup_recommended")

