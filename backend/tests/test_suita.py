"""Suita score (spec §3) — hand-computed totals from the score sheets."""
import pytest

from app.ml.context import ProfileIn
from app.ml.suita import bp_category, suita_score


def P(**kw):
    base = dict(age=50, sex="male", smoker=False, diabetes=False, egfr=80.0, ldl=120.0, hdl=50.0)
    base.update(kw)
    return ProfileIn(**base)


@pytest.mark.parametrize("profile,bp,points,pct,display", [
    # 38 age +0 BP(130/85 normal) +5 LDL120 −5 HDL50 +0 CKD = 38 → 1%
    (P(), (130, 85), 38, 1.0, "1"),
    # 45 +5 smk +6 DM +4 stage1 +10 LDL165 +0 HDL35 +3 eGFR50 = 73 → >28%
    (P(age=60, smoker=True, diabetes=True, ldl=165, hdl=35, egfr=50), (150, 95), 73, 28.0, ">28"),
    # 30 −7 F −7 optimal +0 LDL90 −6 HDL65 = 10 → <1%
    (P(age=40, sex="female", ldl=90, hdl=65, egfr=90), (115, 75), 10, 1.0, "<1"),
    # 53 +5 smk +6 stage2 +7 LDL145 −5 HDL45 = 66 → 22%
    (P(age=72, smoker=True, ldl=145, hdl=45, egfr=70), (165, 90), 66, 22.0, "22"),
    # 51 −7 F +6 DM +0 +11 LDL185 −5 HDL58 +3 eGFR60(stage3) = 59 → 9%
    (P(age=67, sex="female", diabetes=True, ldl=185, hdl=58, egfr=60), (125, 82), 59, 9.0, "9"),
    # 45 +0 +0 +4 (118/92 → stage1 by diastolic) +7 LDL150 +0 HDL39 +14 eGFR25 = 70 → 22%
    (P(age=55, ldl=150, hdl=39, egfr=25), (118, 92), 70, 22.0, "22"),
])
def test_ldl_sheet(profile, bp, points, pct, display):
    res = suita_score(profile, *bp)
    assert res.applicable and res.sheet == "ldl"
    assert res.points == points and res.probability_pct == pct and res.probability_display == display


@pytest.mark.parametrize("profile,bp,points,pct", [
    # 39 +5 smk +3 stage1 +5 TC250 +0 HDL40 +0 = 52 → 10%
    (P(ldl=None, total_chol=250, hdl=40, smoker=True), (145, 85), 52, 10.0),
    # 58 −6 F +5 DM −7 optimal −11 TC150 +6 HDL30 +15 eGFR20 = 60 → 15%
    (P(age=70, sex="female", diabetes=True, ldl=None, total_chol=150, hdl=30, egfr=20), (110, 70), 60, 15.0),
    # 30 +0 TC200 −5 HDL55 = 25 → <1%
    (P(age=40, ldl=None, total_chol=200, hdl=55, egfr=90), (125, 80), 25, 1.0),
])
def test_tc_sheet(profile, bp, points, pct):
    res = suita_score(profile, *bp)
    assert res.applicable and res.sheet == "tc"
    assert res.points == points and res.probability_pct == pct


def test_bp_category_uses_higher_of_sys_dia():
    assert bp_category(119, 79) == "optimal"
    assert bp_category(119, 80) == "normal_high_normal"
    assert bp_category(139, 89) == "normal_high_normal"
    assert bp_category(140, 70) == "stage1"
    assert bp_category(118, 99) == "stage1"
    assert bp_category(160, 70) == "stage2"
    assert bp_category(120, 100) == "stage2"


@pytest.mark.parametrize("age,known_chd,reason", [
    (34, False, "not_applicable_age"), (80, False, "not_applicable_age"), (50, True, "not_applicable_known_chd"),
])
def test_eligibility(age, known_chd, reason):
    res = suita_score(P(age=age, known_chd=known_chd), 130, 80)
    assert not res.applicable and res.reason == reason and res.status == "not_applicable"


def test_eligibility_bounds_inclusive():
    assert suita_score(P(age=35), 130, 80).applicable
    assert suita_score(P(age=79), 130, 80).applicable


def test_missing_lipids_not_imputed():
    res = suita_score(P(ldl=None, hdl=None, total_chol=None), 130, 80)
    assert res.status == "insufficient_data"
    assert set(res.missing_inputs) == {"ldl", "hdl", "total_chol"}


def test_missing_hdl_blocks_both_sheets():
    res = suita_score(P(hdl=None, total_chol=200), 130, 80)
    assert res.status == "insufficient_data" and "hdl" in res.missing_inputs


def test_missing_egfr_is_never_assumed_normal():
    res = suita_score(P(egfr=None), 130, 80)
    assert res.status == "insufficient_data" and res.missing_inputs == ["egfr"]


def test_ckd_stage_used_when_egfr_missing():
    assert suita_score(P(egfr=None, ckd_stage=3), 130, 80).points == 38 + 3


def test_missing_bp_and_profile_fields():
    res = suita_score(ProfileIn(age=None, ldl=100, hdl=50, egfr=80), None, None)
    assert {"age", "sex", "smoker", "diabetes", "bp_7day_mean"} <= set(res.missing_inputs)


def test_what_if_each_change_independent():
    res = suita_score(P(age=60, smoker=True, diabetes=True, ldl=165, hdl=35, egfr=50), 150, 95)
    wi = {w["change"]: (w["points"], w["probability_pct"]) for w in res.what_if}
    assert wi == {
        "stop smoking": (68, 22.0),          # 73 − 5
        "BP < 120/80": (62, 14.0),           # 73 − 4 − 7
        "LDL < 100 mg/dL": (63, 14.0),       # 73 − 10
        "HDL ≥ 60 mg/dL": (67, 22.0),        # 73 − 6
    }


def test_what_if_omits_changes_already_met():
    res = suita_score(P(age=40, sex="female", ldl=90, hdl=65, egfr=90), 115, 75)
    assert res.what_if == []


def test_what_if_tc_sheet_uses_tc_target():
    res = suita_score(P(ldl=None, total_chol=250, hdl=40, smoker=True), 145, 85)
    changes = [w["change"] for w in res.what_if]
    assert "total cholesterol < 160 mg/dL" in changes and "LDL < 100 mg/dL" not in changes
