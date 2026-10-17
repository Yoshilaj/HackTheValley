"""Cardio rules (spec §2, §3, §4): fires / does not fire / insufficient-or-invalid."""
from datetime import timedelta

from app.ml.context import BpReadingIn, ProfileIn
from tests.test_rules_helpers import AS_OF, at, bp_days, ctx, obs, rhythm, run_rule


def C(**kw):
    return ctx("cardio", **kw)


# BP-00 ---------------------------------------------------------------------
def test_bp00_insufficient():
    o = run_rule("BP-00", C(bp_readings=bp_days(4)))
    assert o.status == "insufficient" and o.missing_inputs == ["bp_home_readings"]
    assert o.data == {"n_days": 4, "needed_days": 5}


def test_bp00_ok_with_five_days():
    assert run_rule("BP-00", C(bp_readings=bp_days(5))).status == "not_fired"


def test_bp00_invalid_readings_do_not_count():
    bad = [BpReadingIn(r.measured_at, r.session, r.systolic, r.diastolic, 250, False, True) for r in bp_days(7)]
    assert run_rule("BP-00", C(bp_readings=bad)).status == "insufficient"


# BP-01 ---------------------------------------------------------------------
def test_bp01_fires_grade_i():
    o = run_rule("BP-01", C(bp_readings=bp_days(7, (140, 86), (136, 84))))
    assert o.fired and o.evidence[0]["type"] == "bp_window"


def test_bp01_not_fired_below_or_grade_ii():
    assert run_rule("BP-01", C(bp_readings=bp_days(7, (130, 80), (128, 78)))).status == "not_fired"
    assert run_rule("BP-01", C(bp_readings=bp_days(7, (150, 95), (148, 92)))).status == "not_fired"


def test_bp01_insufficient():
    assert run_rule("BP-01", C(bp_readings=bp_days(3, (140, 86), (140, 86)))).status == "insufficient"


def test_bp01_hands_over_to_bp02_when_sustained():
    rs = bp_days(7, (140, 86), (138, 84)) + bp_days(7, (140, 86), (138, 84), start_offset=7)
    assert run_rule("BP-01", C(bp_readings=rs)).status == "not_fired"
    assert run_rule("BP-02", C(bp_readings=rs)).fired


# BP-02 ---------------------------------------------------------------------
def test_bp02_fires_grade_ii():
    o = run_rule("BP-02", C(bp_readings=bp_days(7, (150, 94), (142, 90))))
    assert o.fired and "146/92" in o.summary


def test_bp02_fires_diastolic_only():
    assert run_rule("BP-02", C(bp_readings=bp_days(7, (130, 92), (130, 90)))).fired


def test_bp02_not_fired_single_grade_i_window():
    assert run_rule("BP-02", C(bp_readings=bp_days(7, (140, 86), (138, 84)))).status == "not_fired"


def test_bp02_insufficient():
    assert run_rule("BP-02", C(bp_readings=bp_days(4, (170, 100), (170, 100)))).status == "insufficient"


# BP-03 ---------------------------------------------------------------------
def test_bp03_fires_morning_hypertension():
    assert run_rule("BP-03", C(bp_readings=bp_days(7, (140, 88), (125, 78)))).fired


def test_bp03_not_fired_when_evening_also_high():
    assert run_rule("BP-03", C(bp_readings=bp_days(7, (140, 88), (136, 80)))).status == "not_fired"


def test_bp03_insufficient_without_evening():
    o = run_rule("BP-03", C(bp_readings=bp_days(7, (140, 88), None)))
    assert o.status == "insufficient" and o.missing_inputs == ["bp_evening_readings"]


# BP-04 ---------------------------------------------------------------------
def test_bp04_fires_15mmhg():
    o = run_rule("BP-04", C(bp_readings=bp_days(7, (135, 80), (120, 75))))
    assert o.fired and o.data["morning_minus_evening_sys"] == 15


def test_bp04_not_fired_14mmhg():
    assert run_rule("BP-04", C(bp_readings=bp_days(7, (134, 80), (120, 75)))).status == "not_fired"


def test_bp04_insufficient():
    assert run_rule("BP-04", C(bp_readings=bp_days(7, None, (120, 75)))).status == "insufficient"


# BP-05 / EMG-02 --------------------------------------------------------------
def severe(minutes_after=None, second=(185, 112)):
    t0 = AS_OF - timedelta(hours=2)
    rs = [BpReadingIn(t0, "evening", 188, 114, 80, False, True, id="r0"),
          BpReadingIn(t0 + timedelta(minutes=1), "evening", 184, 112, 80, False, True, id="r1")]
    if minutes_after is not None:
        rs.append(BpReadingIn(t0 + timedelta(minutes=minutes_after), "evening", *second, 80, False, True, id="r2"))
    return rs


def test_bp05_symptomatic_is_emergency_and_emg02_fires():
    c = C(bp_readings=severe(), observations=[obs(1, "headache")])
    o = run_rule("BP-05", c)
    assert o.fired and o.level == "emergency"
    assert run_rule("EMG-02", c).fired


def test_bp05_asymptomatic_persistent_is_urgent_followup():
    c = C(bp_readings=severe(minutes_after=6))
    o = run_rule("BP-05", c)
    assert o.fired and o.level == "followup_recommended" and o.data["urgent"]
    assert run_rule("EMG-02", c).status == "not_fired"


def test_bp05_asymptomatic_not_remeasured_asks_remeasure():
    o = run_rule("BP-05", C(bp_readings=severe()))
    assert o.status == "insufficient" and o.missing_inputs == ["bp_remeasure_after_5min"] and o.note


def test_bp05_resolved_on_remeasure():
    assert run_rule("BP-05", C(bp_readings=severe(minutes_after=6, second=(150, 90)))).status == "not_fired"


def episode(hours_ago: float, first=(190, 105), second=(188, 104), minutes_after: float | None = 10, tag="e"):
    """A severe occasion ``hours_ago`` with an optional re-measure ``minutes_after`` later."""
    t0 = AS_OF - timedelta(hours=hours_ago)
    rs = [BpReadingIn(t0, "evening", *first, 80, False, True, id=f"{tag}0")]
    if minutes_after is not None:
        rs.append(BpReadingIn(t0 + timedelta(minutes=minutes_after), "evening", *second, 80, False, True, id=f"{tag}1"))
    return rs


def test_bp05_each_severe_reading_is_its_own_occasion():
    """Regression: an earlier episode that came down must not mask a later persistent one
    (the re-measure check used to be anchored on the earliest severe reading in 24 h)."""
    rs = episode(20, (185, 100), (150, 90), tag="old") + episode(0.5, (190, 105), (188, 104), tag="new")
    o = run_rule("BP-05", C(bp_readings=rs))
    assert o.fired and o.level == "followup_recommended" and o.data["urgent"]
    assert [e["ref"] for e in o.evidence] == ["new0", "new1"]


def test_bp05_old_persistent_episode_still_fires_when_new_episode_came_down():
    """Decision: any persistent severe episode inside the 24 h lookback is reported, even when a
    later episode resolved on re-measurement — a confirmed ≥180/110 episode within a day still
    warrants urgent follow-up. Evidence points at the persistent (old) pair only."""
    rs = episode(20, (185, 100), (188, 104), tag="old") + episode(0.5, (190, 105), (150, 90), tag="new")
    o = run_rule("BP-05", C(bp_readings=rs))
    assert o.fired and o.level == "followup_recommended"
    assert [e["ref"] for e in o.evidence] == ["old0", "old1"]
    # ...but once the old episode drops out of the 24 h lookback, only the resolved one remains
    rs = episode(25, (185, 100), (188, 104), tag="old") + episode(0.5, (190, 105), (150, 90), tag="new")
    assert run_rule("BP-05", C(bp_readings=rs)).status == "not_fired"


def test_bp05_latest_severe_without_remeasure_is_insufficient_even_if_old_one_came_down():
    rs = episode(20, (185, 100), (150, 90), tag="old") + episode(0.5, (190, 105), minutes_after=None, tag="new")
    o = run_rule("BP-05", C(bp_readings=rs))
    assert o.status == "insufficient" and o.missing_inputs == ["bp_remeasure_after_5min"]
    assert o.evidence[0]["ref"] == "new0"


def test_bp05_remeasure_outside_window_does_not_count():
    # 4 min: too soon; 61 min: too late → both leave the occasion un-remeasured
    for mins in (4, 61):
        o = run_rule("BP-05", C(bp_readings=episode(2, minutes_after=mins)))
        assert o.status == "insufficient"


def test_bp05_not_fired_normal_or_invalid_or_old():
    assert run_rule("BP-05", C(bp_readings=bp_days(7))).status == "not_fired"
    invalid = [BpReadingIn(AS_OF - timedelta(hours=1), "evening", 270, 120, 80, False, True)]
    assert run_rule("BP-05", C(bp_readings=invalid, observations=[obs(1, "headache")])).status == "not_fired"
    old = [BpReadingIn(at(3, 7), "morning", 190, 115, 80, False, True)]
    assert run_rule("EMG-02", C(bp_readings=old, observations=[obs(1, "chest_pain")])).status == "not_fired"


# FRS-01 (Canada) ---------------------------------------------------------------
FRS = dict(age=61, sex="female", smoker=True, diabetes=False, total_chol=180.0, hdl=47.0, bp_treated=False)


def test_frs01_fires_at_ccs_intermediate():
    o = run_rule("FRS-01", C(profile=ProfileIn(**FRS), bp_readings=bp_days(7, (124, 80), (124, 80))))
    assert o.fired and o.evidence[0]["value"]["category"] == "intermediate"
    assert o.evidence[0]["value"]["percent_10y"] == 10.5


def test_frs01_not_fired_low_risk():
    p = ProfileIn(**(FRS | dict(age=45, smoker=False)))
    assert run_rule("FRS-01", C(profile=p, bp_readings=bp_days(7, (118, 75), (118, 75)))).status == "not_fired"


def test_frs01_insufficient_not_applicable_and_japan():
    o = run_rule("FRS-01", C(profile=ProfileIn(**(FRS | dict(hdl=None))), bp_readings=bp_days(7)))
    assert o.status == "insufficient" and o.missing_inputs == ["hdl"]
    assert run_rule("FRS-01", C(profile=ProfileIn(**(FRS | dict(age=80))), bp_readings=bp_days(7))).status == "not_applicable"
    assert run_rule("FRS-01", C(profile=ProfileIn(**FRS), bp_readings=bp_days(7), region="JP")).status == "not_applicable"


# SUITA-01 (Japan only; kept, not used in Canada) --------------------------------
FULL = dict(age=67, sex="female", smoker=False, diabetes=True, ldl=185.0, hdl=58.0, egfr=60.0)


def test_suita01_fires_at_9pct():
    o = run_rule("SUITA-01", C(profile=ProfileIn(**FULL), bp_readings=bp_days(7, (125, 82), (125, 82))))
    assert o.fired and o.evidence[0]["value"]["probability_pct"] == 9.0


def test_suita01_not_fired_low_risk():
    p = ProfileIn(**(FULL | dict(age=40, diabetes=False, ldl=90.0, hdl=65.0, egfr=90.0)))
    assert run_rule("SUITA-01", C(profile=p, bp_readings=bp_days(7, (115, 75), (115, 75)))).status == "not_fired"


def test_suita01_insufficient_and_not_applicable():
    o = run_rule("SUITA-01", C(profile=ProfileIn(**(FULL | dict(egfr=None))), bp_readings=bp_days(7)))
    assert o.status == "insufficient" and o.missing_inputs == ["egfr"]
    assert run_rule("SUITA-01", C(profile=ProfileIn(**(FULL | dict(known_chd=True))),
                                  bp_readings=bp_days(7))).status == "not_applicable"


# RHY-01 ------------------------------------------------------------------------
def test_rhy01_fires_on_omron_possible_af_without_symptoms():
    o = run_rule("RHY-01", C(rhythm_results=[rhythm(3, "心房細動の可能性", "omron_connect")]))
    assert o.fired and o.evidence[0]["value"] == "possible_af"


def test_rhy01_fires_on_apple_irn_and_ecg_afib():
    assert run_rule("RHY-01", C(rhythm_results=[rhythm(1, "Irregular rhythm", "apple_irn")])).fired
    assert run_rule("RHY-01", C(rhythm_results=[rhythm(1, "Atrial Fibrillation", "apple_ecg")])).fired


def test_rhy01_not_fired_sinus_or_known_af():
    assert run_rule("RHY-01", C(rhythm_results=[rhythm(1, "Sinus rhythm")])).status == "not_fired"
    o = run_rule("RHY-01", C(profile=ProfileIn(known_af=True), rhythm_results=[rhythm(1, "AFib")]))
    assert o.status == "not_fired" and o.note and o.evidence


def test_rhy01_ignores_results_outside_lookback():
    assert run_rule("RHY-01", C(rhythm_results=[rhythm(45, "AFib")])).status == "not_fired"


# RHY-02 ------------------------------------------------------------------------
def test_rhy02_invalid():
    o = run_rule("RHY-02", C(rhythm_results=[rhythm(1, "Inconclusive"), rhythm(2, "Poor Recording")]))
    assert o.status == "invalid" and o.invalid_inputs == ["rhythm_recording"]


def test_rhy02_not_when_a_classified_result_exists():
    rs = [rhythm(1, "Inconclusive"), rhythm(2, "Poor Recording"), rhythm(3, "Sinus Rhythm")]
    assert run_rule("RHY-02", C(rhythm_results=rs)).status == "not_fired"


def test_rhy02_not_with_single_or_old():
    assert run_rule("RHY-02", C(rhythm_results=[rhythm(1, "Inconclusive")])).status == "not_fired"
    assert run_rule("RHY-02", C(rhythm_results=[rhythm(9, "Inconclusive"), rhythm(10, "Inconclusive")])).status == "not_fired"


# RHY-03 ------------------------------------------------------------------------
def test_rhy03_fires():
    assert run_rule("RHY-03", C(rhythm_results=[rhythm(1, "High Heart Rate"), rhythm(5, "Low HR")])).fired


def test_rhy03_not_fired_single():
    assert run_rule("RHY-03", C(rhythm_results=[rhythm(1, "High Heart Rate")])).status == "not_fired"


def test_rhy03_unknown_label_maps_to_other():
    assert run_rule("RHY-03", C(rhythm_results=[rhythm(1, "???", "omron_connect"), rhythm(2, "xx")])).status == "not_fired"
