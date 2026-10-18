"""Keyboard finger-tapping features (spec §5.3, BRAIN test: KS30 / AT30 / IS30)."""

import numpy as np
import pytest

from app.ml.features.tapping import tapping_features, tapping_session_features


def stream(n=60, iti=480.0, dwell=95.0, jitter=10.0, dwell_jitter=5.0, seed=0, freezes=(), release=True,
           keys=("s", ";"), start=100.0):
    """Alternating key events with gaussian jitter; ``freezes`` = tap indices after which a 2 s pause follows."""
    rng = np.random.default_rng(seed)
    out, t = [], start
    for k in range(n):
        d = max(20.0, dwell + dwell_jitter * rng.standard_normal())
        out.append({"key": keys[k % 2], "down_ms": t, "up_ms": (t + d) if release else None})
        t += max(100.0, iti + jitter * rng.standard_normal()) + (2000.0 if k in freezes else 0.0)
    return out


def test_healthy_stream():
    f = tapping_features(stream(), "left")
    assert f["valid"] and f["reason"] is None and f["tap_count"] == 60 and f["kinesia_score"] == 60
    assert abs(f["mean_iti_ms"] - 480) < 8 and f["iti_cv"] < 0.05 and abs(f["decrement_slope_ms_per_tap"]) < 0.3
    assert abs(f["dwell_mean_ms"] - 95) < 3 and 0 < f["dwell_cv"] < 0.1
    assert abs(f["travel_time_mean_ms"] - 385) < 8 and f["travel_time_var_ms2"] < 400 and f["travel_time_cv"] < 0.06
    assert f["n_freezes"] == 0 and f["n_outliers"] == 0 and f["n_autorepeat"] == 0 and f["n_unreleased"] == 0
    assert f["error_presses"] == 0 and f["alternation_error_rate"] == 0.0 and f["input_method"] == "keyboard"


def test_slowed_stream_with_freezes():
    healthy = tapping_features(stream(), "left")
    slow = tapping_features(stream(n=40, iti=650.0, dwell=140.0, freezes=(12, 27)), "left")
    assert slow["valid"] and slow["tap_count"] == 40  # freezes stay in the count
    assert slow["n_freezes"] == 2 and slow["n_outliers"] == 2  # ...but are excluded from the interval stats
    assert abs(slow["mean_iti_ms"] - 650) < 10 and slow["mean_iti_ms"] > healthy["mean_iti_ms"]
    assert slow["dwell_mean_ms"] > healthy["dwell_mean_ms"] + 30
    assert slow["travel_time_var_ms2"] > 100 * healthy["travel_time_var_ms2"]  # raw IS30 keeps the pauses
    assert slow["travel_time_cv"] < 0.2  # MAD-cleaned CV stays stable for the baseline series


def test_decrement_slope_positive():
    ev = [{"key": "s;"[k % 2], "down_ms": t, "up_ms": t + 90} for k, t in enumerate(np.cumsum(400 + 5.0 * np.arange(50)))]
    f = tapping_features(ev, "right")
    assert f["valid"] and 4.0 < f["decrement_slope_ms_per_tap"] < 6.0


def test_autorepeat_rejected():
    ev = stream(n=30)
    held = [{"key": "s", "down_ms": 20000.0 + 25 * k, "up_ms": None} for k in range(12)]  # OS repeat every 25 ms
    f = tapping_features(ev + held, "left")
    assert f["valid"] is False and f["reason"] == "key_autorepeat_detected" and f["n_autorepeat"] == 11
    assert f["tap_count"] == 42 and f["mean_iti_ms"] is None and f["dwell_mean_ms"] is not None


def test_error_presses_and_same_key_repeats():
    ev = stream(n=40)
    ev.insert(10, {"key": "d", "down_ms": ev[10]["down_ms"] - 50, "up_ms": ev[10]["down_ms"] - 10})
    ev.insert(21, {"key": "Semicolon", "down_ms": ev[21]["down_ms"] - 200, "up_ms": ev[21]["down_ms"] - 100})
    f = tapping_features(ev, "left")
    assert f["valid"] and f["n_events"] == 42 and f["tap_count"] == 41 and f["error_presses"] == 1
    assert f["n_same_key_repeats"] == 1 and f["alternation_error_rate"] == pytest.approx(2 / 42)


def test_unreleased_and_null_up():
    f = tapping_features(stream(release=False), "left")
    assert f["valid"] and f["n_unreleased"] == 60 and f["dwell_mean_ms"] is None and f["dwell_cv"] is None
    assert f["travel_time_var_ms2"] is None and f["mean_iti_ms"] is not None
    ev = stream(n=30)
    ev[-1]["up_ms"] = None  # key still held at the end
    assert tapping_features(ev, "left")["n_unreleased"] == 1


def test_kinesia_score_scales_with_duration():
    f = tapping_features(stream(n=40, iti=480.0), "left", duration_s=20)
    assert f["valid"] and f["tap_count"] == 40 and f["kinesia_score"] == pytest.approx(60)
    assert tapping_features(stream(n=100), "left", duration_s=30)["tap_count"] == 63  # later presses ignored


def test_validity_gates():
    assert tapping_features(stream(n=40), "left", duration_s=15)["reason"] == "duration_below_20_s"
    f = tapping_features(stream(n=9), "left")
    assert f["valid"] is False and f["reason"] == "fewer_than_10_taps" and f["tap_count"] == 9
    assert tapping_features([], "right")["reason"] == "fewer_than_10_taps"
    assert tapping_features([{"key": "s", "down_ms": "x"}], "right")["n_events"] == 0


def test_session_asymmetry():
    s = tapping_session_features(stream(n=60, seed=1), stream(n=45, iti=640.0, dwell=120.0, seed=2))
    assert s["valid"] and s["keys"] == ["s", ";"] and s["duration_s"] == 30
    assert abs(s["asymmetry"] - (60 - 45) / 60) < 1e-9 and s["iti_asymmetry"] > 0.2 and s["dwell_asymmetry"] > 0.1
    bad = tapping_session_features(stream(), stream(n=3))
    assert bad["valid"] is False and bad["asymmetry"] is None and bad["reason"] == "right:fewer_than_10_taps"
