"""Dashboard + risk routes (spec phase-a §11.4)."""
import uuid
from datetime import date, datetime, timedelta, timezone

from app.core.auth import create_access_token
from app.db.models import Appointment, Consent, Profile, User
from app.services import dashboard_service as ds
from app.services import risk_service as rs
from scripts.seed import DEMO_PROFILE, _dob_for_age
from scripts.seed_demo import seed_planted_trend

SPEC_KEYS = ["pathway", "level", "priority_score", "score_kind", "score_label", "headline", "drivers",
             "missing_inputs", "data_quality", "trend", "probabilities", "long_term_risk", "preventive_measures",
             "next_action", "disclaimer"]


def make_user(db, planted=False) -> User:
    u = User(email=f"db-{uuid.uuid4().hex[:8]}@example.com")
    u.profile = Profile(**(DEMO_PROFILE | {"date_of_birth": _dob_for_age(68)}))
    db.add(u)
    db.flush()
    for p in ("data_processing", "ai_assessment"):
        db.add(Consent(user_id=u.id, purpose=p))
    db.commit()
    if planted:
        seed_planted_trend(db, u)
    return u


def hdr(u):
    return {"Authorization": f"Bearer {create_access_token(u.id)}"}


def test_dashboard_assesses_on_demand_and_shape(client, db):
    u = make_user(db)
    r = client.get("/api/dashboard?language=en", headers=hdr(u))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"pathways", "metrics_summary", "appointments", "notifications"}
    assert [c["pathway"] for c in body["pathways"]] == ["cardio", "cognitive", "parkinsons", "general"]
    for c in body["pathways"]:
        assert list(c) == SPEC_KEYS
        assert len(c["trend"]) == 8 and set(c["trend"][0]) == {"week", "priority_score"}
        assert c["score_kind"] == "follow_up_priority"
        assert c["score_label"] == "Follow-up priority (not a disease probability)"
    assert body["pathways"][1]["next_action"] == {"type": "collect_data", "appointment_id": None}
    assert isinstance(body["notifications"]["unread"], int)


def test_planted_card_matches_spec(client, db):
    u = make_user(db, planted=True)
    r = client.get("/api/dashboard?language=en", headers=hdr(u))
    cardio = r.json()["pathways"][0]
    assert cardio["level"] == "followup_recommended" and cardio["priority_score"] == 70
    assert cardio["headline"] == "Two findings suggest a check-up is worth arranging"
    assert {d["rule_id"] for d in cardio["drivers"]} == {"BP-02", "RHY-01"}
    assert all(d["evidence_ids"] for d in cardio["drivers"])
    frs = cardio["probabilities"][0]
    assert frs["source_id"] == "src_framingham2008" and "what_if" in frs
    lt = cardio["long_term_risk"]
    assert lt["calculator"] == "framingham_2008" and lt["status"] == "ok"
    # Robert, 68, current smoker, TC 5.8 / HDL 1.15 mmol/L, untreated home SBP ≈146 → CCS high
    assert lt["category"] == "high" and lt["percent_10y"] >= 20 and lt["display"]
    assert lt["inputs"]["total_chol_mmol_l"] == 5.8 and lt["inputs"]["hdl_mmol_l"] == 1.15
    assert lt["inputs"]["bp_treated"] is False
    assert {w["change"] for w in lt["what_if"]} >= {"stop smoking", "systolic BP 120 mmHg"}
    assert all(c["long_term_risk"] is None for c in r.json()["pathways"][1:])
    assert any(i["rule_id"] == "FRS-01" for i in
               rs.latest_assessments(db, u.id)["cardio"].data_quality["informational"])
    assert cardio["preventive_measures"] and set(cardio["preventive_measures"][0]) == {"text", "source_id"}
    scores = [t["priority_score"] for t in cardio["trend"]]
    assert scores[-1] == 70 and (25 in scores or 35 in scores)  # seeded prior watch weeks
    pd = r.json()["pathways"][2]
    assert all(p.get("detail_only") for p in pd["probabilities"])
    ja = client.get("/api/dashboard?language=ja", headers=hdr(u)).json()["pathways"][0]
    assert ja["score_label"].startswith("受診の優先度") and "診断ではありません" in ja["disclaimer"]


def test_next_action_appointment(db):
    u = make_user(db, planted=True)
    cardio = rs.assess_user(db, u)[1]
    appt = Appointment(user_id=u.id, assessment_id=cardio.id, status="requested", clinic={"name": "Clinic A"})
    db.add(appt)
    db.commit()
    card = ds.pathway_card(db, u, cardio, "en")
    assert card["next_action"] == {"type": "appointment_requested", "appointment_id": appt.id}
    appt.status = "confirmed"
    db.commit()
    assert ds.pathway_card(db, u, cardio, "en")["next_action"]["type"] == "appointment_confirmed"
    dash = ds.dashboard(db, u, "en")
    assert dash["appointments"] == []  # confirmed is not pending
    for status, kind in (("awaiting_approval", "appointment_approval_needed"),
                         ("needs_user_info", "appointment_needs_info")):
        appt.status = status
        db.commit()
        assert ds.pathway_card(db, u, cardio, "en")["next_action"] == {"type": kind, "appointment_id": appt.id}
        assert [a["status"] for a in ds.dashboard(db, u, "en")["appointments"]] == [status]


def test_trend_length_and_weeks(db):
    u = make_user(db)
    rs.assess_user(db, u)
    t = ds.weekly_trend(db, u, "cardio")
    assert len(t) == 8
    weeks = [date.fromisoformat(p["week"]) for p in t]
    assert all(w.weekday() == 0 for w in weeks) and weeks == sorted(weeks)


def test_risk_routes(client, db, admin_headers):
    u = make_user(db, planted=True)
    h = hdr(u)
    run = client.post("/api/risk/run", headers=h)
    assert run.status_code == 200, run.text
    cardio = next(a for a in run.json()["assessments"] if a["pathway"] == "cardio")
    assert cardio["level"] == "followup_recommended"
    latest = client.get("/api/risk/latest?language=en", headers=h).json()
    assert [c["pathway"] for c in latest] == ["cardio", "cognitive", "parkinsons", "general"]
    hist = client.get("/api/risk/history?weeks=8", headers=h).json()
    assert {"week", "pathway", "level", "priority_score", "assessment_id"} == set(hist[0])
    one = client.get(f"/api/risk/{cardio['id']}", headers=h)
    assert one.status_code == 200 and one.json()["explanation"]
    other = make_user(db)
    assert client.get(f"/api/risk/{cardio['id']}", headers=hdr(other)).status_code == 404
    # admin may run for another user; a plain user may not
    assert client.post(f"/api/risk/run?user_id={u.id}", headers=hdr(other)).status_code == 403
    assert client.post(f"/api/risk/run?user_id={u.id}", headers=admin_headers).status_code == 200
    assert client.post("/api/risk/run").status_code == 401
    assert client.get("/api/dashboard").status_code == 401
