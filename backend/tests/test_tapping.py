"""Finger-tapping features (spec §5.3)."""

import numpy as np

from app.ml.features.tapping import tapping_features, tapping_session_features


def regular(n=60, iti=250.0, jitter=5.0, seed=0):
    rng = np.random.default_rng(seed)
    return list(np.cumsum(iti + jitter * rng.standard_normal(n)))


def decrementing(n=45, start=200.0, step=6.0):
    return list(np.cumsum(start + step * np.arange(n)))


def test_regular_sequence():
    f = tapping_features(regular(), "left")
    assert f["valid"] and f["tap_count"] == 60
    assert abs(f["mean_iti_ms"] - 250) < 5
    assert f["iti_cv"] < 0.05
    assert abs(f["decrement_slope_ms_per_tap"]) < 0.5
    assert f["n_outliers"] == 0


def test_decrementing_sequence_has_positive_slope():
    f = tapping_features(decrementing(), "right", duration_s=15)
    assert f["valid"]
    assert 5.0 < f["decrement_slope_ms_per_tap"] < 7.0
    assert f["iti_cv"] > tapping_features(regular(), "left")["iti_cv"]


def test_outlier_pause_is_excluded():
    ts = regular(n=40)
    ts = ts[:20] + [t + 2000 for t in ts[20:]]  # one 2 s pause
    f = tapping_features(ts, "left")
    assert f["n_outliers"] == 1 and abs(f["mean_iti_ms"] - 250) < 10


def test_taps_after_duration_ignored():
    f = tapping_features(regular(n=100, iti=250.0, jitter=0.0), "left", duration_s=15)
    assert f["tap_count"] == 61  # taps at t0 .. t0+15 s


def test_too_few_taps_invalid():
    f = tapping_features([0, 300, 600], "left")
    assert f["valid"] is False and "fewer_than_5" in f["reason"] and f["mean_iti_ms"] is None
    assert tapping_features([], "right")["valid"] is False


def test_session_asymmetry():
    s = tapping_session_features(regular(n=60), regular(n=45, iti=330.0))
    assert s["valid"]
    assert abs(s["asymmetry"] - (60 - 45) / 60) < 1e-9
    bad = tapping_session_features(regular(), [1, 2])
    assert bad["valid"] is False and bad["asymmetry"] is None and "right" in bad["reason"]
