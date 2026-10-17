"""Engine contract (spec §8): level ordering, independence, emergency, persistence."""
from app.ml.context import ProfileIn
from app.ml.engine import consecutive_windows, evaluate_all, evaluate_pathway, level_rank
from app.ml.priority import priority_score
from tests.test_rules_helpers import (
    AS_OF, bp_days, ctx, daily_series, obs, prior, rhythm, screener, story_sessions, vowel, wobble,
)
from tests.test_rules_cardio import severe

GRADE_II = bp_days(7, (150, 94), (142, 90))
NORMAL = bp_days(7, (122, 76), (118, 74))
AF = [rhythm(3, "心房細動の可能性", "omron_connect")]


def test_level_order():
    order = ["no_finding", "watch", "followup_recommended", "emergency"]
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


def test_emergency_dominates_and_is_not_combined():
    r = evaluate_pathway(ctx("cardio", bp_readings=GRADE_II + severe(), rhythm_results=AF,
                             observations=[obs(1, "chest_pain")]))
    assert r.level == "emergency"
    assert {"BP-05", "EMG-02", "BP-02", "RHY-01"} <= set(r.fired_rule_ids)
    assert priority_score(r) == 100


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


def test_bp05_asymptomatic_urgent_followup_level():
    r = evaluate_pathway(ctx("cardio", bp_readings=NORMAL + severe(minutes_after=6)))
    fr = {f.id: f.level for f in r.fired_rules}
    assert fr["BP-05"] == "followup_recommended" and "EMG-02" not in fr


def test_pd03_escalates_only_with_other_finding():
    voice = [vowel(20, 0.8), vowel(5, 0.7)]
    alone = evaluate_pathway(ctx("parkinsons", task_sessions=voice))
    # review fix 13: voice data exists, so PD is not insufficient_data; voice alone never raises
    # the level (→ no_finding) and the missing screener / mobility inputs are optional
    assert "PD-03" not in alone.fired_rule_ids and alone.level == "no_finding"
    assert "pd_screener" in alone.data_quality["missing_optional_inputs"]
    assert any(i["rule_id"] == "PD-03" for i in alone.informational)
    both = evaluate_pathway(ctx("parkinsons", task_sessions=voice,
                                screeners=[screener({"tremor_rest": True, "soft_voice": True})]))
    assert both.fired_rule_ids == ["PD-01", "PD-03"] and both.level == "watch"
    assert both.probabilities[0]["kind"] == "experimental_model_score"


def test_pd_watch_with_gait():
    s = {"walking_speed": daily_series("walking_speed", wobble(1.2, 30, 0.02), [1.0] * 7)}
    r = evaluate_pathway(ctx("parkinsons", series=s,
                             screeners=[screener({"tremor_rest": True, "constipation": True})]))
    assert r.fired_rule_ids == ["PD-01", "PD-02"] and priority_score(r) == 35
    assert r.data_quality["baseline_n_days"]["walking_speed"] == 30


def test_cognitive_functional_change_fires_without_baseline():
    r = evaluate_pathway(ctx("cognitive", task_sessions=story_sessions([70, 71]),
                             observations=[obs(10, "missed_appointments", "functional_change")]))
    assert r.level == "followup_recommended" and "story_recall_sessions" in r.missing_inputs


def test_cognitive_retest_prompt_is_informational():
    r = evaluate_pathway(ctx("cognitive", task_sessions=story_sessions([70, 72, 71, 69, 73, 50])))
    assert "COG-04" not in r.fired_rule_ids
    assert any(i["rule_id"] == "COG-04" for i in r.informational)


def test_general_emergency():
    r = evaluate_pathway(ctx("general", observations=[obs(1, "face_droop")]))
    assert r.level == "emergency" and r.fired_rule_ids == ["EMG-01"]


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
