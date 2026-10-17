"""Framingham Risk Score (D'Agostino 2008 general CVD; CCS 2021 usage) — app/ml/framingham.py.

Reference values
----------------
* FHS official calculator ``gencardio_lipids.xls`` (framinghamheartstudy.org, prepared by
  R.B. D'Agostino and M.J. Pencina), sheet "Sheet2": age 30, SBP 125, TC 180 mg/dL,
  HDL 45 mg/dL, non-smoker, no diabetes →
  women untreated 0.01309508819972538, women treated 0.017545543966781985,
  men untreated 0.016541031859781286, men treated 0.022654152504833047.
* Worked examples commonly cited from the 2008 paper: woman 61 y, TC 180, HDL 47, SBP 124
  untreated, smoker, no diabetes → 10.48 %; man 53 y, TC 161, HDL 55, SBP 125 treated,
  non-smoker, diabetes → 15.62 %.
"""
import pytest

from app.ml.context import ProfileIn
from app.ml.framingham import (
    COEFFICIENTS, ccs_category, framingham_probability_entry, framingham_score, frs_risk, mg_dl_to_mmol_l,
    mmol_l_to_mg_dl,
)


@pytest.mark.parametrize("sex,treated,expected", [
    ("female", False, 0.01309508819972538),
    ("female", True, 0.017545543966781985),
    ("male", False, 0.016541031859781286),
    ("male", True, 0.022654152504833047),
])
def test_matches_fhs_calculator_spreadsheet(sex, treated, expected):
    assert frs_risk(sex, 30, 180, 45, 125, treated, False, False) == pytest.approx(expected, rel=1e-9)


def test_paper_worked_examples():
    assert round(100 * frs_risk("female", 61, 180, 47, 124, False, True, False), 2) == 10.48
    assert round(100 * frs_risk("male", 53, 161, 55, 125, True, False, True), 2) == 15.62


def test_published_coefficients():
    f, m = COEFFICIENTS["female"], COEFFICIENTS["male"]
    assert (f.ln_age, f.ln_total_chol, f.ln_hdl, f.ln_sbp_untreated, f.ln_sbp_treated, f.smoker, f.diabetes) == \
        (2.32888, 1.20904, -0.70833, 2.76157, 2.82263, 0.52873, 0.69154)
    assert (f.s0_10y, f.mean_sum) == (0.95012, 26.1931)
    assert (m.ln_age, m.ln_total_chol, m.ln_hdl, m.ln_sbp_untreated, m.ln_sbp_treated, m.smoker, m.diabetes) == \
        (3.06117, 1.12370, -0.93263, 1.93303, 1.99881, 0.65451, 0.57367)
    assert (m.s0_10y, m.mean_sum) == (0.88936, 23.9802)


def test_unit_conversion():
    assert mg_dl_to_mmol_l(193.35) == pytest.approx(5.0, abs=1e-3)
    assert mmol_l_to_mg_dl(1.3) == pytest.approx(50.27, abs=0.01)


def test_ccs_categories():
    assert [ccs_category(x) for x in (9.9, 10.0, 19.9, 20.0)] == ["low", "intermediate", "intermediate", "high"]


WOMAN = dict(age=61, sex="female", smoker=True, diabetes=False, total_chol=180.0, hdl=47.0, bp_treated=False)


def test_score_profile_with_what_if_and_mmol_inputs():
    r = framingham_score(ProfileIn(**WOMAN), 124.0)
    assert r.status == "ok" and r.percent_10y == 10.5 and r.base_percent_10y == 10.5
    assert r.category == "intermediate" and not r.family_history_doubled
    assert r.inputs["total_chol_mmol_l"] == pytest.approx(4.65, abs=0.01)
    assert r.inputs["hdl_mmol_l"] == pytest.approx(1.22, abs=0.01)
    changes = {w["change"]: w for w in r.what_if}
    assert set(changes) == {"stop smoking", "systolic BP 120 mmHg", "HDL cholesterol 1.3 mmol/L"}
    assert changes["stop smoking"]["percent_10y"] < r.percent_10y
    assert "family history" in " ".join(r.notes).lower()  # unknown → stated, not applied


def test_ccs_family_history_doubles_only_30_to_59_without_diabetes():
    p = ProfileIn(**(WOMAN | dict(age=55, family_history_premature_cvd=True)))
    r = framingham_score(p, 124.0)
    assert r.family_history_doubled and r.percent_10y == pytest.approx(2 * r.base_percent_10y, abs=0.11)
    assert not framingham_score(ProfileIn(**(WOMAN | dict(family_history_premature_cvd=True))), 124.0).family_history_doubled
    assert not framingham_score(ProfileIn(**(WOMAN | dict(age=55, diabetes=True, family_history_premature_cvd=True))),
                                124.0).family_history_doubled


def test_insufficient_and_not_applicable():
    r = framingham_score(ProfileIn(**(WOMAN | dict(total_chol=None, bp_treated=None))), 124.0)
    assert r.status == "insufficient_data" and r.missing_inputs == ["bp_treated", "total_chol"]
    assert framingham_score(ProfileIn(**WOMAN), None).missing_inputs == ["bp_7day_mean"]
    assert framingham_score(ProfileIn(**(WOMAN | dict(age=75))), 124.0).reason == "not_applicable_age"
    assert framingham_score(ProfileIn(**(WOMAN | dict(known_chd=True))), 124.0).status == "not_applicable"


def test_probability_entry_shape():
    e = framingham_probability_entry(framingham_score(ProfileIn(**WOMAN), 124.0))
    assert e["calculator"] == "framingham_2008" and e["kind"] == "population_estimate"
    assert e["value_pct"] == 10.5 and e["value_display"] == "10.5" and e["category"] == "intermediate"
    assert e["source_id"] == "src_framingham2008" and e["guideline_source_id"] == "src_ccs_lipids_2021"
    assert all({"change", "value_pct", "value_display"} <= set(w) for w in e["what_if"])
