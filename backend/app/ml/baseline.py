"""Personal-baseline change detector (spec §6, HearO-informed).

Robust baseline = median and scaled MAD (×1.4826) of daily values in the
``baseline_days`` before the recent window (the recent ``recent_days`` are
excluded by default). A change is flagged when ≥``min_recent_hits`` recent
daily values deviate ≥``k``·MAD in the adverse direction.

Window decision: "trailing 30 days excluding the last 7" is implemented as the
30 calendar days *before* the 7-day recent window (days −36..−7 relative to
``as_of`` with day 0 = as_of), so the baseline has the full 30 days.

Minimum detectable deviation (MAD == 0 / resolution floor): a scaled MAD of
0 (or one smaller than the data's resolution) would make any deviation look
significant — on an integer 1–5 scale a baseline of mostly 2s with some 3s has
MAD 0, yet a week of 3s is not a change. The effective ``scale`` is therefore
floored at the data resolution, where ``step`` is the smallest positive
difference between the sorted distinct values of baseline ∪ recent:

* baseline has ≥2 distinct values → ``scale = max(1.4826·MAD, step)`` so the
  threshold ``k·scale`` is at least ``k`` resolution steps
  (``note='resolution_floor'`` when the floor is binding; continuous metrics
  with MAD ≥ step are unaffected).
* baseline is perfectly flat (all values identical) → the baseline carries no
  spread information, so a recent value must differ from the median by at
  least one resolution step, floored at ``k·MAD_ZERO_FRACTION`` (5%) of
  |median| for continuous data: ``scale = max(0.05·|median|, step/k)``
  (``note='mad_zero_fallback'``).
* flat baseline, median 0 and no step (every value identical) → nothing can be
  detected: ``scale = 0``, ``no_change`` with ``note='zero_variability'``.

Output wording is "change from your usual range worth checking" — never a
disease name.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median
from typing import Iterable, Literal

from app.ml.context import DailySeries

MAD_SCALE = 1.4826
MAD_ZERO_FRACTION = 0.05
CHANGE_TEXT = "change from your usual range worth checking"


def resolution_step(values: Iterable[float]) -> float:
    """Smallest positive difference between distinct values (0 if all identical)."""
    uniq = sorted(set(values))
    return min((b - a for a, b in zip(uniq, uniq[1:])), default=0.0)


def robust_center_scale(
    values: Iterable[float], recent: Iterable[float] = (), k: float = 1.5,
) -> tuple[float, float, float, str | None]:
    """Return (median, scaled MAD, effective scale, note) for the baseline ``values``.

    ``scale`` is defined so that ``k·scale`` is the minimum adverse deviation
    (see module docstring). ``recent`` only sharpens the resolution estimate
    and, for a flat baseline, supplies the step; ``k`` is only used for a flat
    baseline so that one resolution step is enough.
    """
    vals = list(values)
    med = median(vals)
    mad = MAD_SCALE * median(abs(v - med) for v in vals)
    step = resolution_step([*vals, *recent])
    if len(set(vals)) > 1:
        if mad >= step:
            return med, mad, mad, None
        return med, mad, step, "resolution_floor"
    fallback = max(MAD_ZERO_FRACTION * abs(med), step / k)
    if fallback > 0:
        return med, mad, fallback, "mad_zero_fallback"
    return med, mad, 0.0, "zero_variability"


def is_adverse(value: float, med: float, scale: float, k: float, direction: str) -> bool:
    if scale <= 0:
        return False
    threshold = med - k * scale if direction == "down" else med + k * scale
    if math.isclose(value, threshold, rel_tol=1e-9, abs_tol=1e-12):
        return True
    return value <= threshold if direction == "down" else value >= threshold


def daily_values(series: DailySeries) -> dict[date, float]:
    """Reduce multiple points per day to the daily median."""
    by_day: dict[date, list[float]] = {}
    for d, v in series.points:
        if v is None:
            continue
        by_day.setdefault(d, []).append(float(v))
    return {d: median(vs) for d, vs in sorted(by_day.items())}


@dataclass(frozen=True)
class BaselineResult:
    status: Literal["insufficient_data", "no_change", "change"]
    baseline_median: float | None
    mad: float | None
    recent_values: list[float] = field(default_factory=list)
    n_hits: int = 0
    n_baseline_days: int = 0
    scale: float | None = None
    note: str | None = None
    name: str = ""
    direction: str = "up"


def baseline_change(
    series: DailySeries,
    as_of: datetime | date,
    baseline_days: int = 30,
    recent_days: int = 7,
    exclude_recent: bool = True,
    min_baseline_days: int = 14,
    k: float = 1.5,
    direction: Literal["up", "down"] = "up",
    min_recent_hits: int = 5,
) -> BaselineResult:
    """Detect a persistent adverse change vs the personal baseline (spec §6).

    ``insufficient_data`` when the baseline has <``min_baseline_days`` days or
    fewer than ``min_recent_hits`` recent days exist (a change could not be
    detected even in principle).
    """
    end = as_of.date() if isinstance(as_of, datetime) else as_of
    recent_start = end - timedelta(days=recent_days - 1)
    base_end = recent_start - timedelta(days=1) if exclude_recent else end
    base_start = base_end - timedelta(days=baseline_days - 1)
    dv = daily_values(series)
    recent = [v for d, v in dv.items() if recent_start <= d <= end]
    base = [v for d, v in dv.items() if base_start <= d <= base_end]
    common = dict(recent_values=recent, n_baseline_days=len(base), name=series.name, direction=direction)
    if len(base) < min_baseline_days or len(recent) < min_recent_hits:
        return BaselineResult("insufficient_data", None, None, **common)
    med, mad, scale, note = robust_center_scale(base, recent, k)
    hits = sum(1 for v in recent if is_adverse(v, med, scale, k, direction))
    status: Literal["no_change", "change"] = "change" if hits >= min_recent_hits else "no_change"
    return BaselineResult(status, med, mad, n_hits=hits, scale=scale, note=note, **common)
