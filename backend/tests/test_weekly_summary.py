"""Monday weekly job: re-assess (trigger ``weekly``) + one ``weekly_summary`` notification per user."""
from sqlalchemy import select

from app.db.models import PATHWAYS, Notification, RiskAssessment
from app.jobs import run_job
from tests.test_dashboard import make_user


def test_weekly_summary_assesses_and_notifies(db):
    u = make_user(db, planted=True)
    row = run_job("weekly_summary_notifications")
    assert row.status == "done", row.error
    assert row.result["notified"] >= 1 and row.result["assessed"] >= 1

    note = db.scalar(select(Notification).where(
        Notification.user_id == u.id, Notification.kind == "weekly_summary").order_by(Notification.created_at.desc()))
    assert note is not None and note.title == "Your weekly summary"
    assert "This week:" in note.body and "Heart & blood pressure: follow-up recommended (" in note.body
    p = note.payload
    assert set(p["levels"]) == set(PATHWAYS) == set(p["previous_levels"]) == set(p["scores"])
    assert p["levels"]["cardio"] == "followup_recommended"  # the seed back-fills earlier assessments
    assert all(isinstance(s, int) for s in (p["scores"]["cardio"], p["scores"]["parkinsons"]))
    assert p["tasks"].get("finger_tap", 0) >= 1 and p["bp_days"] >= 5 and p["checkins"] >= 5
    assert len(p["week"]) == 10  # Monday ISO date

    weekly = [r for r in db.scalars(select(RiskAssessment).where(RiskAssessment.user_id == u.id))
              if (r.data_quality or {}).get("trigger") == "weekly"]
    assert {r.pathway for r in weekly} == set(PATHWAYS)
