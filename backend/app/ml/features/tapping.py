"""Finger-tapping task features.

Spec: docs/specs/phase-a-assessments.md §5.3 — 15 s per hand, alternate
index-finger taps on two on-screen targets, pointer timestamps recorded.
Features: tap count, mean inter-tap interval (ITI), ITI coefficient of
variation, decrement (linear slope of ITI over taps), left/right asymmetry of
count. No trained model (mPower access gated, §9); outputs feed the personal
baseline rule PD-04 only.

Outlier ITIs (pauses, double-registrations) are detected with a robust
median/MAD rule and excluded from mean/CV/slope but counted in ``n_outliers``.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

MIN_TAPS = 5
_MAD_K = 3.5
_MAD_SCALE = 1.4826


def _invalid(hand: str, reason: str, tap_count: int) -> dict[str, Any]:
    return {
        "hand": hand,
        "valid": False,
        "reason": reason,
        "tap_count": int(tap_count),
        "mean_iti_ms": None,
        "iti_cv": None,
        "decrement_slope_ms_per_tap": None,
        "n_outliers": 0,
    }


def tapping_features(timestamps_ms: Sequence[float], hand: str, duration_s: float = 15) -> dict[str, Any]:
    """Features for one hand's tapping trial.

    Args:
        timestamps_ms: pointer-down timestamps in milliseconds (any origin).
        hand: "left" or "right" (stored verbatim).
        duration_s: trial length; taps later than ``duration_s`` after the first tap are ignored.

    Returns:
        dict with ``tap_count, mean_iti_ms, iti_cv, decrement_slope_ms_per_tap,
        n_outliers, valid, reason, hand``. With fewer than 5 taps, ``valid`` is
        False and the interval features are None.
    """
    ts = np.asarray([float(t) for t in timestamps_ms if t is not None and np.isfinite(float(t))], dtype=np.float64)
    ts = np.sort(ts)
    if len(ts):
        ts = ts[ts - ts[0] <= duration_s * 1000.0]
    n = int(len(ts))
    if n < MIN_TAPS:
        return _invalid(hand, f"fewer_than_{MIN_TAPS}_taps", n)

    iti = np.diff(ts)
    med = float(np.median(iti))
    mad = float(np.median(np.abs(iti - med))) * _MAD_SCALE
    if mad > 0:
        outlier = np.abs(iti - med) > _MAD_K * mad
    else:
        outlier = np.zeros(len(iti), dtype=bool)
    outlier |= iti <= 0  # duplicate timestamps
    keep = ~outlier
    clean = iti[keep]
    if len(clean) < MIN_TAPS - 1:
        out = _invalid(hand, "too_many_outlier_intervals", n)
        out["n_outliers"] = int(outlier.sum())
        return out

    mean_iti = float(np.mean(clean))
    cv = float(np.std(clean, ddof=1) / mean_iti) if len(clean) > 1 and mean_iti > 0 else 0.0
    idx = np.arange(len(iti))[keep].astype(np.float64)
    slope = float(np.polyfit(idx, clean, 1)[0]) if len(clean) >= 2 else 0.0
    return {
        "hand": hand,
        "valid": True,
        "reason": None,
        "tap_count": n,
        "mean_iti_ms": mean_iti,
        "iti_cv": cv,
        "decrement_slope_ms_per_tap": slope,
        "n_outliers": int(outlier.sum()),
    }


def _asym(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    m = max(a, b)
    return float(abs(a - b) / m) if m > 0 else 0.0


def tapping_session_features(
    left: Sequence[float], right: Sequence[float], duration_s: float = 15
) -> dict[str, Any]:
    """Both-hand session: per-hand features plus asymmetry = |L-R| / max(L, R) of tap count.

    ``iti_asymmetry`` (same formula on mean ITI) is reported when both hands are valid.
    ``valid`` is True only when both hands are valid.
    """
    lf = tapping_features(left, "left", duration_s)
    rf = tapping_features(right, "right", duration_s)
    valid = bool(lf["valid"] and rf["valid"])
    reasons = [f"{h['hand']}:{h['reason']}" for h in (lf, rf) if not h["valid"]]
    return {
        "left": lf,
        "right": rf,
        "asymmetry": _asym(float(lf["tap_count"]), float(rf["tap_count"])) if valid else None,
        "iti_asymmetry": _asym(lf["mean_iti_ms"], rf["mean_iti_ms"]) if valid else None,
        "valid": valid,
        "reason": "; ".join(reasons) if reasons else None,
    }
