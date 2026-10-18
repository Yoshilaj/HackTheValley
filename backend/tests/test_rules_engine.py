"""Engine contract (spec §8): level ordering, independence, persistence."""
from app.ml.context import ProfileIn
from app.ml.engine import consecutive_windows, evaluate_all, evaluate_pathway, level_rank
from app.ml.priority import priority_score
from tests.test_rules_helpers import (
    AS_OF, bp_days, ctx, daily_series, obs, prior, rhythm, story_sessions, vowel, wobble,
)
from tests.test_rules_cardio import severe

GRADE_II = bp_days(7, (150, 94), (142, 90))
NORMAL = bp_days(7, (122, 76), (118, 74))
AF = [rhythm(3, "心房細動の可能性", "omron_connect")]


def test_level_order():
    order = ["no_finding", "watch", "followup_recommended"]
    assert sorted(order, key=level_rank) == order


def test_demo_user_bp02_rhy01_followup():
    r = evaluate_pathway(ctx("cardio", bp_readings=GRADE_II, rhythm_results=AF))
    assert r.fired_rule_ids == ["BP-02", "RHY-01"] and r.level == "followup_recommended"
    assert priority_score(r) == 70


def test_bp02_fires_independently_of_rhythm():
    r = evaluate_pathway(ctx("cardio", bp_readings=GRADE_II))
    assert r.fired_rule_ids == ["BP-02"] and r.level == "followup_recommended"


def test_rhy01_fires_without_palpitations_or_bp_and_missing_reported_alongside():
    r = evaluate_pathway(ctx("cardio", rhythm_results=AF))
    assert r.fired_rule_ids == ["RHY-01"] and r.level == "followup_recommended"
    assert "bp_home_readings" in r.missing_inputs


def test_missing_assessment_is_never_no_finding():
    r = evaluate_pathway(ctx("cardio"))
    assert r.level == "insufficient_data" and priority_score(r) is None
    assert r.data_quality["valid_bp_days"] == 0


def test_no_finding_with_complete_normal_data():
    r = evaluate_pathway(ctx("cardio", bp_readings=NORMAL))
    assert r.level == "no_finding" and r.fired_rules == [] and priority_score(r) == 0


def test_invalid_measurement_when_nothing_fired():
    r = evaluate_pathway(ctx("cardio", bp_readings=NORMAL, rhythm_results=[rhythm(1, "Inconclusive"), rhythm(2, "Poor Recording")]))
    assert r.level == "invalid_measurement" and r.invalid_inputs == ["rhythm_recording"]
    assert any(i["rule_id"] == "RHY-02" for i in r.informational)


def test_severe_bp_with_symptoms_is_followup_not_emergency():
    r = evaluate_pathway(ctx("cardio", bp_readings=GRADE_II + severe(minutes_after=6), rhythm_results=AF,
                             observations=[obs(1, "chest_pain")]))
    assert r.level == "followup_recommended"
    assert {"BP-05", "BP-02", "RHY-01"} <= set(r.fired_rule_ids) and "EMG-02" not in r.fired_rule_ids
    assert priority_score(r) == 80  # three follow-up rules: 60 + 10 + 10


def test_framingham_is_informational_only_in_canada():
    p = ProfileIn(age=61, sex="female", smoker=True, diabetes=False, total_chol=180, hdl=47, bp_treated=False)
    r = evaluate_pathway(ctx("cardio", profile=p, bp_readings=bp_days(7, (124, 80), (124, 80))))
    assert r.level == "no_finding"
    assert [i["rule_id"] for i in r.informational] == ["FRS-01"]
    [prob] = r.probabilities
    assert prob["calculator"] == "framingham_2008" and prob["value_pct"] == 10.5
    assert prob["source_id"] == "src_framingham2008" and "SUITA-01" not in r.outcomes


def test_suita_is_informational_only_in_japan():
    p = ProfileIn(age=67, sex="female", smoker=False, diabetes=True, ldl=185, hdl=58, egfr=60)
    r = evaluate_pathway(ctx("cardio", profile=p, bp_readings=bp_days(7, (125, 82), (125, 82)), region="JP"))
    assert r.level == "no_finding"
    assert [i["rule_id"] for i in r.informational] == ["SUITA-01"]
    assert r.probabilities[0]["value_pct"] == 9.0 and r.probabilities[0]["source_id"] == "src_suita2014"


def test_long_term_missing_inputs_do_not_make_pathway_insufficient():
    r = evaluate_pathway(ctx("cardio", bp_readings=NORMAL))
    assert r.level == "no_finding" and "age" in r.data_quality["context_missing_inputs"]
    assert r.probabilities[0]["status"] == "insufficient_data"


def test_bp05_urgent_followup_level():
    r = evaluate_pathway(ctx("cardio", bp_readings=NORMAL + severe(minutes_after=6)))
    fr = {f.id: f.level for f in r.fired_rules}
    assert fr["BP-05"] == "followup_recommended"


def test_pd03_escalates_only_with_other_finding(monkeypatch):
    voice = [vowel(20, 0.8), vowel(5, 0.7)]
    alone = evaluate_pathway(ctx("parkinsons", task_sessions=voice))
    # review fix 13: voice data exists, so PD is not insufficient_data; voice alone never raises
    # the level (→ no_finding) and the missing mobility / tapping inputs are optional
    assert "PD-03" not in alone.fired_rule_ids and alone.level == "no_finding"
    assert "tap_count" in alone.data_quality["missing_optional_inputs"]
    assert any(i["rule_id"] == "PD-03" for i in alone.informational)
    tapping = {"tap_count": daily_series("tap_count", wobble(60, 30, 2), [45] * 7)}
    with_tapping = ctx("parkinsons", task_sessions=voice, series=tapping)
    # default config: voice model is display-only, so PD-03 notes but does not fire
    default = evaluate_pathway(with_tapping)
    assert default.fired_rule_ids == ["PD-04"] and default.level == "watch"
    assert any(i["rule_id"] == "PD-03" for i in default.informational)
    from app.core.config import settings

    monkeypatch.setattr(settings, "pd_vowel_rule_enabled", True)
    both = evaluate_pathway(with_tapping)
    assert both.fired_rule_ids == ["PD-04", "PD-03"] and both.level == "watch"
    assert both.probabilities[0]["kind"] == "experimental_model_score"


def test_pd_watch_with_gait():
    s = {"walking_speed": daily_series("walking_speed", wobble(1.2, 30, 0.02), [1.0] * 7)}
    r = evaluate_pathway(ctx("parkinsons", series=s))
    assert r.fired_rule_ids == ["PD-02"] and priority_score(r) == 25
    assert r.data_quality["baseline_n_days"]["walking_speed"] == 30


def test_cognitive_functional_change_fires_without_baseline():
    r = evaluate_pathway(ctx("cognitive", task_sessions=story_sessions([70, 71]),
                             observations=[obs(10, "missed_appointments", "functional_change")]))
    assert r.level == "followup_recommended" and "story_recall_sessions" in r.missing_inputs


def test_cognitive_retest_prompt_is_informational():
    r = evaluate_pathway(ctx("cognitive", task_sessions=story_sessions([70, 72, 71, 69, 73, 50])))
    assert "COG-04" not in r.fired_rule_ids
    assert any(i["rule_id"] == "COG-04" for i in r.informational)


def test_general_symptom_observation_does_not_escalate():
    r = evaluate_pathway(ctx("general", observations=[obs(1, "face_droop", "symptom")]))
    assert r.level == "insufficient_data" and r.fired_rule_ids == []


def test_persistence_counts_consecutive_weekly_windows():
    priors = [prior(7, ["BP-02"], "followup_recommended"), prior(14, ["BP-02"], "followup_recommended")]
    r = evaluate_pathway(ctx("cardio", bp_readings=GRADE_II, prior_assessments=priors))
    assert r.persistence == {"BP-02": 3}
    assert consecutive_windows("BP-02", AS_OF, [prior(14, ["BP-02"])]) == 1  # gap in window 1
    assert consecutive_windows("BP-02", AS_OF, [prior(3, ["BP-02"])]) == 1   # too recent for a prior week
    assert consecutive_windows("BP-02", AS_OF, [prior(8, [{"id": "BP-02"}])]) == 2


def test_evaluate_all():
    out = evaluate_all([ctx("cardio", bp_readings=GRADE_II), ctx("general")])
    assert [r.pathway for r in out] == ["cardio", "general"]


def test_pd03b_in_engine_display_only_alone_watch_with_tapping():
    """PD-03B (spec §5.5): alone → no_finding + informational message; with PD-04 → watch [PD-04, PD-03B]."""
    from tests.test_rules_helpers import ADVERSE_STORY, ADVERSE_VOWEL, story_baseline, voice_baseline

    voice = voice_baseline() + ADVERSE_VOWEL + story_baseline() + ADVERSE_STORY
    alone = evaluate_pathway(ctx("parkinsons", task_sessions=voice))
    assert alone.level == "no_finding" and "PD-03B" not in alone.fired_rule_ids
    note = next(i for i in alone.informational if i["rule_id"] == "PD-03B")
    assert note["message"]["en"]["title"] == "Change in voice measurements" and note["message"]["ja"]["body"]
    assert len(note["evidence"]) == 5
    dq = alone.data_quality
    assert dq["voice_baseline_sessions"] == {"sustained_vowel": 10, "story_recall": 10}
    assert dq["voice_valid_sessions"] == {"sustained_vowel": 13, "story_recall": 13}
    assert dq["voice_baseline_needed"] == 10 and dq["voice_device"] == "iPhone" and dq["voice_sessions_excluded_noise"] == 0
    assert "voice_baseline_sessions" not in dq["missing_optional_inputs"]
    tapping = {"tap_count": daily_series("tap_count", wobble(60, 30, 2), [45] * 7)}
    both = evaluate_pathway(ctx("parkinsons", task_sessions=voice, series=tapping))
    assert both.fired_rule_ids == ["PD-04", "PD-03B"] and both.level == "watch" and priority_score(both) == 35
    assert all(f.level == "watch" for f in both.fired_rules)
    assert both.fired_rules[1].sources[0] == "src_skodda2013"


def test_pd03b_insufficient_is_optional_not_a_gate():
    from tests.test_rules_helpers import voice_baseline

    s = {"walking_speed": daily_series("walking_speed", wobble(1.2, 30, 0.02), [1.0] * 7)}
    r = evaluate_pathway(ctx("parkinsons", task_sessions=voice_baseline(4), series=s))
    assert r.fired_rule_ids == ["PD-02"] and r.level == "watch"
    assert "voice_baseline_sessions" in r.data_quality["missing_optional_inputs"]
    assert r.data_quality["voice_baseline_sessions"] == {"sustained_vowel": 4, "story_recall": 0}
    empty = evaluate_pathway(ctx("parkinsons"))
    assert empty.level == "insufficient_data" and "voice_baseline_sessions" in empty.missing_inputs
    assert empty.data_quality["voice_baseline_sessions"] == {"sustained_vowel": 0, "story_recall": 0}
