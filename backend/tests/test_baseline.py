"""Personal-baseline change detector (spec §6)."""
import pytest

from app.ml.baseline import baseline_change, resolution_step, robust_center_scale
from app.ml.context import DailySeries
from tests.test_rules_helpers import AS_OF, daily_series, day, wobble


def test_insufficient_baseline_days():
    s = daily_series("walking_speed", wobble(1.2, 13, 0.02), [0.8] * 7, n_baseline=13)
    res = baseline_change(s, AS_OF, direction="down")
    assert res.status == "insufficient_data" and res.n_baseline_days == 13


def test_insufficient_recent_days():
    s = daily_series("walking_speed", wobble(1.2, 30, 0.02), [0.8] * 4)
    assert baseline_change(s, AS_OF, direction="down").status == "insufficient_data"


def test_no_change():
    s = daily_series("walking_speed", wobble(1.2, 30, 0.02), wobble(1.2, 7, 0.02))
    res = baseline_change(s, AS_OF, direction="down")
    assert res.status == "no_change" and res.baseline_median == pytest.approx(1.2)


def test_change_down():
    s = daily_series("walking_speed", wobble(1.2, 30, 0.02), [1.0] * 5 + [1.2] * 2)
    res = baseline_change(s, AS_OF, direction="down")
    assert res.status == "change" and res.n_hits == 5


def test_four_of_seven_is_not_enough():
    s = daily_series("walking_speed", wobble(1.2, 30, 0.02), [1.0] * 4 + [1.2] * 3)
    assert baseline_change(s, AS_OF, direction="down").status == "no_change"


def test_change_up_and_direction_matters():
    s = daily_series("double_support_pct", wobble(28, 30, 0.5), [33] * 7)
    assert baseline_change(s, AS_OF, direction="up").status == "change"
    assert baseline_change(s, AS_OF, direction="down").status == "no_change"


def test_recent_window_excluded_from_baseline():
    s = daily_series("x", wobble(10, 30, 0.5), [20] * 7)
    res = baseline_change(s, AS_OF, direction="up")
    assert res.baseline_median == pytest.approx(10) and res.n_baseline_days == 30


def test_mad_uses_1_4826_constant():
    med, mad, scale, note = robust_center_scale([1, 2, 3, 4, 5])
    assert med == 3 and mad == pytest.approx(1.4826) and note is None


def test_mad_zero_fallback_to_fraction_of_median():
    s = daily_series("x", [10.0] * 30, [10.4] * 7)  # +4% < 1.5 × 5% fallback
    res = baseline_change(s, AS_OF, direction="up")
    assert res.mad == 0 and res.note == "mad_zero_fallback" and res.status == "no_change"
    s2 = daily_series("x", [10.0] * 30, [11.0] * 7)  # +10% ≥ 7.5%
    assert baseline_change(s2, AS_OF, direction="up").status == "change"


def test_everything_identical_is_zero_variability():
    s = daily_series("x", [0.0] * 30, [0.0] * 7)
    res = baseline_change(s, AS_OF, direction="up")
    assert res.status == "no_change" and res.note == "zero_variability" and res.scale == 0


# --- resolution floor on coarse (integer) scales -----------------------------
INT_BASELINE = [2, 2, 2, 3, 2, 3] * 5  # 1–5 fatigue-style scale: median 2, MAD 0, 3 occurs in baseline


def test_integer_scale_value_seen_in_baseline_is_not_a_change():
    """(a) a week of 3s against a baseline of 2s with some 3s must not fire."""
    res = baseline_change(daily_series("fatigue", INT_BASELINE, [3] * 7), AS_OF, direction="up")
    assert res.mad == 0 and res.scale == 1 and res.note == "resolution_floor"
    assert res.status == "no_change" and res.n_hits == 0


def test_integer_scale_large_jump_is_a_change():
    """(b) a week of 5s against the same baseline fires (≥1.5 steps above the median)."""
    res = baseline_change(daily_series("fatigue", INT_BASELINE, [5] * 7), AS_OF, direction="up")
    assert res.status == "change" and res.n_hits == 7
    # 4 is 2 steps above the median of 2 and never occurs in the baseline → change
    assert baseline_change(daily_series("fatigue", INT_BASELINE, [4] * 7), AS_OF, direction="up").status == "change"


def test_median_zero_baseline_with_jumps_is_a_change():
    """(c) a flat zero baseline must not silence a real jump (old: scale 0 → never fires)."""
    res = baseline_change(daily_series("x", [0.0] * 30, [4.0] * 7), AS_OF, direction="up")
    assert res.status == "change" and res.n_hits == 7 and res.note == "mad_zero_fallback"
    # one step is enough from a flat baseline, but values equal to the median never count
    assert baseline_change(daily_series("x", [0.0] * 30, [0, 0, 0, 1, 1, 1, 1]), AS_OF, direction="up").status == "no_change"
    assert baseline_change(daily_series("x", [0.0] * 30, [1.0] * 7), AS_OF, direction="up").status == "change"


def test_continuous_metric_with_mad_unchanged_by_floor():
    """(d) when MAD ≥ resolution step the scale is exactly the scaled MAD, note None."""
    base = wobble(1.2, 30, 0.02)
    med, mad, scale, note = robust_center_scale(base, [1.0] * 7)
    assert mad > 0 and scale == mad and note is None
    assert robust_center_scale(base)[2] == scale  # recent values do not alter the scale
    res = baseline_change(daily_series("walking_speed", base, [1.0] * 5 + [1.2] * 2), AS_OF, direction="down")
    assert res.status == "change" and res.n_hits == 5 and res.scale == pytest.approx(mad) and res.note is None


def test_resolution_step_helper():
    assert resolution_step([2, 3, 2, 2]) == 1
    assert resolution_step([1.0, 1.25, 2.0]) == 0.25
    assert resolution_step([7, 7, 7]) == 0
    assert resolution_step([]) == 0


def test_multiple_points_per_day_use_daily_median():
    pts = [(day(i), v) for i in range(7, 37) for v in (9.0, 10.0, 11.0)] + [(day(i), 10.0) for i in range(7)]
    res = baseline_change(DailySeries("x", pts), AS_OF, direction="up")
    assert res.n_baseline_days == 30 and res.baseline_median == 10.0
