"""Demo replay (spec phase-a §8): planted demo user → cardio [BP-02, RHY-01] → followup_recommended,
parkinsons [PD-04, PD-03B] → watch (simulated voice trend, spec §5.5)."""
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
    assert rows["general"].level != "emergency" and rows["cognitive"].level == "no_finding"
    pd = rows["parkinsons"]
    assert pd.level == "watch" and [f["id"] for f in pd.fired_rules] == ["PD-04", "PD-03B"] and pd.priority_score == 35
    voice = next(f for f in pd.fired_rules if f["id"] == "PD-03B")
    feats = {e["value"]["feature"]: e["value"] for e in voice["evidence"] if e["type"] == "voice_trend_feature"}
    assert set(feats) == {"MDVP:Shimmer", "NHR", "CPPS", "speech_rate", "pause_ratio"}
    assert all(f["status"] == "adverse" and f["n_adverse"] == 3 for f in feats.values())
    assert feats["MDVP:Shimmer"]["k"] == 3.0 and feats["NHR"]["k"] == 3.0
    assert pd.data_quality["voice_baseline_sessions"] == {"sustained_vowel": 10, "story_recall": 10}
    assert pd.data_quality["voice_device"] == "iPhone"
    note = next(i for i in pd.data_quality["informational"] if i["rule_id"] == "PD-03B")
    assert note["message"]["ja"]["title"] and "voice stability, not disease progression" in note["summary"]
    # the v1 model score stays display-only and below threshold
    [prob] = pd.probabilities
    assert prob["kind"] == "experimental_model_score" and prob["detail_only"] and prob["value"] < prob["threshold"]


def test_seed_rows_are_labelled_simulated(db):
    from app.db.models import AssessmentTask
    from scripts.seed_demo import VOICE_SESSION_OFFSETS, tap_features_for, vowel_features_for

    u = make_demo_like(db)
    seed_planted_trend(db, u)
    tasks = list(db.scalars(select(AssessmentTask).where(AssessmentTask.user_id == u.id)))
    by_type = {}
    for t in tasks:
        by_type.setdefault(t.task_type, []).append(t)
    assert len(by_type["sustained_vowel"]) == len(VOICE_SESSION_OFFSETS) == 12 and len(by_type["story_recall"]) == 12
    assert len(by_type["finger_tap"]) == 35
    assert all((t.raw_json or {}).get("simulated") is True for t in tasks)
    assert all(t.recording_quality["device"] == "iPhone" for t in by_type["sustained_vowel"] + by_type["story_recall"])
    assert vowel_features_for(11, True)["MDVP:Shimmer"] > 2 * vowel_features_for(1, False)["MDVP:Shimmer"]
    assert vowel_features_for(11, True)["CPPS"] < vowel_features_for(1, False)["CPPS"] - 3
    # keyboard BRAIN tapping: same shape as a real submission, per-input-method device label
    taps = by_type["finger_tap"]
    assert all(t.valid and t.protocol_version == "tapping-2" and t.recording_quality["device"] == "keyboard"
               and t.raw_json["input_method"] == "keyboard" and t.raw_json["keys"] == ["s", ";"] for t in taps)
    assert all(t.raw_json["left"]["events"][0]["key"] == "s" for t in taps)
    dropped, stable = tap_features_for(0), tap_features_for(20)
    assert dropped["left"]["tap_count"] < stable["left"]["tap_count"] - 10
    assert dropped["left"]["dwell_mean_ms"] > stable["left"]["dwell_mean_ms"] + 30 and dropped["left"]["n_freezes"] == 1


def test_demo_report_shows_voice_table(db):
    from app.providers.llm import FakeLlm
    from app.services.report_service import generate_report

    u = make_demo_like(db)
    seed_planted_trend(db, u)
    pd = {r.pathway: r for r in assess_user(db, u)}["parkinsons"]
    report = generate_report(db, u, pd, FakeLlm(), "en")
    md = report.markdown
    assert "### Voice measures vs personal baseline (experimental, PD-03B)" in md
    assert "| shimmer | sustained_vowel | higher |" in md and "| speech rate | story_recall | lower |" in md
    assert "3/5 | shifted" in md and "expected alert rate with no true change" in md
    assert "cannot determine whether you have Parkinson's disease" in md
    assert "reports voice stability, not progression" in md
    assert "Skodda S" in md and "Barsties" in md and "Telemonitoring" in md
    assert "Sustained-vowel voice model (experimental)" in md  # v1 score stays in the model section
    assert "Voice measures vs personal baseline" in report.html and "flagrow" in report.html

