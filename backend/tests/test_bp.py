"""Home BP windows (spec §2)."""
from datetime import timedelta

import pytest

from app.ml import bp as bpm
from app.ml.context import BpReadingIn
from tests.test_rules_helpers import AS_OF, at, bp_days


def r(sys_, dia, pulse=70, valid=True, session="morning", when=None):
    return BpReadingIn(when or at(0, 7), session, sys_, dia, pulse, False, valid)


@pytest.mark.parametrize("reading,reason", [
    (r(120, 80), None),
    (r(80, 80), "systolic_not_above_diastolic"),
    (r(69, 40), "systolic_out_of_range"),
    (r(261, 100), "systolic_out_of_range"),
    (r(120, 80, pulse=29), "pulse_out_of_range"),
    (r(120, 80, pulse=201), "pulse_out_of_range"),
    (r(120, 80, valid=False), "user_marked_error"),
    (r(120, 80, pulse=None), None),
])
def test_validity(reading, reason):
    assert bpm.reading_validity(reading) == (reason is None, reason)


def test_occasion_mean_uses_valid_readings_and_max_three():
    t = at(0, 7)
    rs = [BpReadingIn(t + timedelta(minutes=i), "morning", s, d, 70, False, True)
          for i, (s, d) in enumerate([(140, 90), (130, 80), (300, 80), (120, 70), (100, 60)])]
    occ = bpm.occasion_means(rs)
    assert len(occ) == 1
    # invalid 300 dropped; first 3 valid = 140,130,120
    assert occ[0].systolic == pytest.approx(130) and occ[0].diastolic == pytest.approx(80)
    assert occ[0].n_readings == 3


def test_day_mean_is_mean_of_morning_and_evening():
    rs = bp_days(1, morning=(150, 90), evening=(130, 80))
    dm = bpm.day_means(bpm.occasion_means(rs))
    assert list(dm.values()) == [(140.0, 85.0)]


def test_day_mean_with_only_one_session():
    rs = bp_days(1, morning=(150, 90), evening=None)
    assert list(bpm.day_means(bpm.occasion_means(rs)).values()) == [(150.0, 90.0)]


def test_window_means_and_counts():
    rs = bp_days(7, morning=(150, 94), evening=(130, 82))
    rs.append(BpReadingIn(at(1, 8), "morning", 70, 80, 70, False, True))  # invalid (sys ≤ dia)
    w = bpm.bp_window(rs, AS_OF)
    assert w.n_days == 7 and w.sufficient
    assert w.systolic_mean == pytest.approx(140) and w.diastolic_mean == pytest.approx(88)
    assert (w.morning_sys, w.morning_dia, w.evening_sys, w.evening_dia) == (150, 94, 130, 82)
    assert w.morning_sys - w.evening_sys == 20  # morning–evening difference
    assert w.n_valid == 28 and w.n_invalid == 1
    assert w.max_reading.systolic == 150


def test_window_excludes_older_days():
    rs = bp_days(7, morning=(120, 70), evening=(120, 70)) + bp_days(7, (200, 120), (200, 120), start_offset=7)
    w = bpm.bp_window(rs, AS_OF)
    assert w.systolic_mean == pytest.approx(120)


def test_window_insufficient_days():
    w = bpm.bp_window(bp_days(4), AS_OF)
    assert w.n_days == 4 and not w.sufficient and w.systolic_mean is None and w.morning_sys is None


def test_window_five_days_is_enough():
    assert bpm.bp_window(bp_days(5, start_offset=2), AS_OF).sufficient


def test_classify_verified_cutoffs_only():
    assert bpm.HOME_CLASSIFICATION_TABLE_VERIFIED is False
    assert bpm.classify_home_bp(134, 84) == "below_threshold"
    assert bpm.classify_home_bp(135, 70) == "grade_i_or_above"
    assert bpm.classify_home_bp(120, 85) == "grade_i_or_above"
    assert bpm.classify_home_bp(145, 80) == "grade_ii_or_above"
    assert bpm.classify_home_bp(130, 90) == "grade_ii_or_above"


def test_classify_full_table_when_enabled(monkeypatch):
    monkeypatch.setattr(bpm, "HOME_CLASSIFICATION_TABLE_VERIFIED", True)
    assert bpm.classify_home_bp(110, 70) == "normal"
    assert bpm.classify_home_bp(120, 70) == "high_normal"
    assert bpm.classify_home_bp(110, 80) == "elevated"
    assert bpm.classify_home_bp(140, 70) == "grade_i"
    assert bpm.classify_home_bp(150, 70) == "grade_ii"
    assert bpm.classify_home_bp(120, 100) == "grade_iii"
