"""Finger-tapping task features — keyboard BRAIN tap test (spec §5.3).

Protocol (Giovannoni 1999 JNNP 67:624; Noyce 2014 PLoS One 9:e96260, PMC4004565): the user
alternately presses the ``s`` and ``;`` keys with the index finger of one hand as fast and as
accurately as possible for 30 s, then repeats with the other hand. The client records every
keydown / keyup (``performance.now()``-relative ms). Keyboard only — no pointer / touch input.

Per hand:

* ``tap_count``        — presses of a target key (= Kinesia Score KS30 when ``duration_s`` = 30);
* ``kinesia_score``    — ``tap_count`` rescaled to 30 s (``tap_count * 30 / duration_s``);
* ``mean_iti_ms`` / ``iti_cv`` / ``decrement_slope_ms_per_tap`` — inter-tap (keydown→keydown)
  interval mean, CV and linear slope, after the median/MAD outlier rule (``n_outliers``);
* ``n_freezes``        — intervals > 3× the median ITI (pauses / freezes). They are reported, excluded
  from the interval statistics, and still counted in ``tap_count``;
* ``dwell_mean_ms`` / ``dwell_cv`` — keydown→keyup hold time (Akinesia Time AT30 = ``dwell_mean_ms``);
* ``travel_time_var_ms2`` — variance of the travelling time (keyup of one key → keydown of the other
  key) over alternate presses = Incoordination Score IS30 (raw, pauses included as in the paper);
  ``travel_time_cv`` — CV of the travelling times after the same MAD outlier rule;
* ``alternation_error_rate`` — (consecutive same-key presses + non-target key presses) / all presses;
* ``error_presses``    — presses of keys other than the two target keys (not taps);
* ``n_autorepeat``     — inter-tap intervals < 30 ms: physically impossible for one finger, so the trial
  is rejected as held-key OS autorepeat.

Validity per hand: ≥10 taps, ``duration_s`` ≥ 20, no autorepeat, enough clean intervals.
No trained model (mPower access gated, §9); outputs feed the personal baseline rule PD-04 only.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

TARGET_KEYS: tuple[str, str] = ("s", ";")
REFERENCE_DURATION_S = 30.0
MIN_TAPS = 10
MIN_DURATION_S = 20.0
AUTOREPEAT_MS = 30.0
FREEZE_FACTOR = 3.0
_MAD_K = 3.5
_MAD_SCALE = 1.4826
#: lower-cased ``KeyboardEvent.code`` names accepted as the target keys ("Semicolon", "KeyS")
_KEY_ALIASES = {"semicolon": ";", "keys": "s"}

_NUMERIC = ("mean_iti_ms", "iti_cv", "decrement_slope_ms_per_tap", "dwell_mean_ms", "dwell_cv",
            "travel_time_mean_ms", "travel_time_var_ms2", "travel_time_cv", "alternation_error_rate")


def normalize_key(key: Any) -> str:
    k = str(key or "").strip()
    if len(k) > 1:
        k = _KEY_ALIASES.get(k.lower(), k.lower())
    return k.lower()


def _event_tuple(e: Any) -> tuple[str, float, float | None] | None:
    """(key, down_ms, up_ms|None) from a dict / pydantic model / sequence; None when unusable."""
    if isinstance(e, Mapping):
        key, down, up = e.get("key"), e.get("down_ms"), e.get("up_ms")
    elif isinstance(e, (list, tuple)):
        key, down, up = (list(e) + [None, None, None])[:3]
    else:
        key, down, up = getattr(e, "key", None), getattr(e, "down_ms", None), getattr(e, "up_ms", None)
    try:
        d = float(down)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(d):
        return None
    u: float | None = None
    if up is not None:
        try:
            u = float(up)
        except (TypeError, ValueError):
            u = None
        if u is not None and (not np.isfinite(u) or u < d):
            u = None  # released before pressed: unusable dwell
    return normalize_key(key), d, u


def _mad_mask(x: np.ndarray) -> np.ndarray:
    """True where ``x`` is a median/MAD outlier."""
    if len(x) == 0:
        return np.zeros(0, dtype=bool)
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * _MAD_SCALE
    if mad > 0:
        return np.abs(x - med) > _MAD_K * mad
    return np.zeros(len(x), dtype=bool)


def _cv(x: np.ndarray) -> float | None:
    if len(x) < 2:
        return 0.0 if len(x) == 1 else None
    m = float(np.mean(x))
    return float(np.std(x, ddof=1) / m) if m > 0 else None


def _base(hand: str, duration_s: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "hand": hand, "valid": False, "reason": None, "input_method": "keyboard", "duration_s": float(duration_s),
        "n_events": 0, "tap_count": 0, "kinesia_score": 0.0, "error_presses": 0, "n_same_key_repeats": 0,
        "n_autorepeat": 0, "n_outliers": 0, "n_freezes": 0, "n_unreleased": 0,
    }
    out.update({k: None for k in _NUMERIC})
    return out


def tapping_features(events: Sequence[Any], hand: str, duration_s: float = REFERENCE_DURATION_S,
                     keys: Sequence[str] = TARGET_KEYS) -> dict[str, Any]:
    """Features for one hand's keyboard tapping trial.

    Args:
        events: key events ``{"key", "down_ms", "up_ms" | None}`` (dicts, models or ``[key, down, up]``).
        hand: "left" or "right" (stored verbatim).
        duration_s: trial length; presses later than ``duration_s`` after the first press are ignored.
        keys: the two target keys (default ``("s", ";")``); other keys count as ``error_presses``.
    """
    targets = {normalize_key(k) for k in keys}
    out = _base(hand, duration_s)
    parsed = [t for t in (_event_tuple(e) for e in events) if t is not None]
    parsed.sort(key=lambda t: t[1])
    if parsed:
        t0 = parsed[0][1]
        parsed = [t for t in parsed if t[1] - t0 <= duration_s * 1000.0]
    out["n_events"] = len(parsed)
    taps = [t for t in parsed if t[0] in targets]
    n = len(taps)
    out["tap_count"] = n
    out["error_presses"] = len(parsed) - n
    out["kinesia_score"] = float(n * REFERENCE_DURATION_S / duration_s) if duration_s > 0 else 0.0
    out["n_same_key_repeats"] = sum(1 for a, b in zip(taps, taps[1:]) if a[0] == b[0])
    out["n_unreleased"] = sum(1 for t in taps if t[2] is None)
    if parsed:
        out["alternation_error_rate"] = float((out["n_same_key_repeats"] + out["error_presses"]) / len(parsed))

    down = np.asarray([t[1] for t in taps], dtype=np.float64)
    iti = np.diff(down)
    out["n_autorepeat"] = int(np.sum(iti < AUTOREPEAT_MS)) if len(iti) else 0

    # dwell (akinesia time) — independent of validity of the interval statistics
    dwell = np.asarray([t[2] - t[1] for t in taps if t[2] is not None], dtype=np.float64)
    if len(dwell):
        out["dwell_mean_ms"] = float(np.mean(dwell))
        out["dwell_cv"] = _cv(dwell)

    if duration_s < MIN_DURATION_S:
        out["reason"] = f"duration_below_{int(MIN_DURATION_S)}_s"
        return out
    if n < MIN_TAPS:
        out["reason"] = f"fewer_than_{MIN_TAPS}_taps"
        return out
    if out["n_autorepeat"]:
        out["reason"] = "key_autorepeat_detected"
        return out

    # inter-tap intervals: MAD outliers excluded from mean/CV/slope, freezes counted
    med = float(np.median(iti))
    out["n_freezes"] = int(np.sum(iti > FREEZE_FACTOR * med)) if med > 0 else 0
    outlier = _mad_mask(iti) | (iti <= 0)
    keep = ~outlier
    clean = iti[keep]
    out["n_outliers"] = int(outlier.sum())
    if len(clean) < MIN_TAPS - 1:
        out["reason"] = "too_many_outlier_intervals"
        return out
    mean_iti = float(np.mean(clean))
    out["mean_iti_ms"] = mean_iti
    out["iti_cv"] = _cv(clean)
    idx = np.arange(len(iti))[keep].astype(np.float64)
    out["decrement_slope_ms_per_tap"] = float(np.polyfit(idx, clean, 1)[0]) if len(clean) >= 2 else 0.0

    # travelling time between alternate presses: keyup of one key → keydown of the other (IS30)
    travel = np.asarray([b[1] - a[2] for a, b in zip(taps, taps[1:]) if a[0] != b[0] and a[2] is not None],
                        dtype=np.float64)
    if len(travel) >= 2:
        out["travel_time_mean_ms"] = float(np.mean(travel))
        out["travel_time_var_ms2"] = float(np.var(travel, ddof=1))
        clean_travel = travel[~_mad_mask(travel)]
        out["travel_time_cv"] = _cv(clean_travel) if len(clean_travel) >= 2 else None
    out["valid"] = True
    return out


def _asym(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    m = max(abs(a), abs(b))
    return float(abs(a - b) / m) if m > 0 else 0.0


def tapping_session_features(left: Sequence[Any], right: Sequence[Any], duration_s: float = REFERENCE_DURATION_S,
                             keys: Sequence[str] = TARGET_KEYS) -> dict[str, Any]:
    """Both-hand session: per-hand features plus asymmetry = |L-R| / max(L, R) of tap count.

    ``iti_asymmetry`` and ``dwell_asymmetry`` (same formula on mean ITI / mean dwell) are reported
    when both hands are valid. ``valid`` is True only when both hands are valid.
    """
    lf = tapping_features(left, "left", duration_s, keys)
    rf = tapping_features(right, "right", duration_s, keys)
    valid = bool(lf["valid"] and rf["valid"])
    reasons = [f"{h['hand']}:{h['reason']}" for h in (lf, rf) if not h["valid"]]
    return {
        "input_method": "keyboard", "keys": [normalize_key(k) for k in keys], "duration_s": float(duration_s),
        "left": lf,
        "right": rf,
        "asymmetry": _asym(float(lf["tap_count"]), float(rf["tap_count"])) if valid else None,
        "iti_asymmetry": _asym(lf["mean_iti_ms"], rf["mean_iti_ms"]) if valid else None,
        "dwell_asymmetry": _asym(lf["dwell_mean_ms"], rf["dwell_mean_ms"]) if valid else None,
        "valid": valid,
        "reason": "; ".join(reasons) if reasons else None,
    }
