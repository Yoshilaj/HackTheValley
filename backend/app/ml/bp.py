"""Home blood-pressure windows (spec §2, JSH 2019 home-BP protocol).

Pipeline: reading → validity → *occasion* mean (valid readings of one
session on one calendar day) → *day* mean (mean of the morning and evening
occasions present) → *window* mean over the last ``days`` calendar days,
reported only when ≥5 days are present (JSH: "7 days, minimum 5").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import fmean
from typing import Iterable

from app.ml.context import BpReadingIn

# --- Validity (spec §2 'Validity') -------------------------------------------
SYS_MIN, SYS_MAX = 70, 260
PULSE_MIN, PULSE_MAX = 30, 200
#: JSH 2019: 2 readings per occasion (3 allowed, ≥4 discouraged) → use first 3.
MAX_READINGS_PER_OCCASION = 3

# --- Window (spec §2: "7 days (minimum 5 days)") -----------------------------
WINDOW_DAYS = 7
MIN_WINDOW_DAYS = 5

# --- Verified home thresholds (spec §2; JSH 2019 text) ----------------------
HOME_HTN_SYS, HOME_HTN_DIA = 135, 85  # home hypertension ≥135/85
GRADE_II_SYS, GRADE_II_DIA = 145, 90  # Grade II home ≥145/90

#: JSH 2019 Table 2-5 home classification is UNVERIFIED (spec §2). Keep False
#: until the table is checked against JSH 2019/2025 primary text.
HOME_CLASSIFICATION_TABLE_VERIFIED = False

SESSIONS = ("morning", "evening")


def reading_validity(r: BpReadingIn) -> tuple[bool, str | None]:
    """Return ``(valid, invalid_reason)`` per spec §2 'Validity'."""
    if not r.valid:
        return False, "user_marked_error"
    if r.systolic <= r.diastolic:
        return False, "systolic_not_above_diastolic"
    if r.systolic < SYS_MIN or r.systolic > SYS_MAX:
        return False, "systolic_out_of_range"
    if r.pulse is not None and (r.pulse < PULSE_MIN or r.pulse > PULSE_MAX):
        return False, "pulse_out_of_range"
    return True, None


def is_valid_reading(r: BpReadingIn) -> bool:
    return reading_validity(r)[0]


@dataclass(frozen=True)
class Occasion:
    """Mean of the valid readings of one session on one day."""

    day: date
    session: str
    systolic: float
    diastolic: float
    n_readings: int
    reading_ids: tuple[str | None, ...] = ()
    first_at: datetime | None = None


def occasion_means(readings: Iterable[BpReadingIn]) -> list[Occasion]:
    """Group valid morning/evening readings by (day, session) and average them.

    Uses at most the first ``MAX_READINGS_PER_OCCASION`` valid readings by time.
    Readings with any other ``session`` value are ignored.
    """
    groups: dict[tuple[date, str], list[BpReadingIn]] = {}
    for r in readings:
        if r.session not in SESSIONS or not is_valid_reading(r):
            continue
        groups.setdefault((r.measured_at.date(), r.session), []).append(r)
    out: list[Occasion] = []
    for (d, s), rs in sorted(groups.items()):
        rs = sorted(rs, key=lambda x: x.measured_at)[:MAX_READINGS_PER_OCCASION]
        out.append(
            Occasion(
                day=d,
                session=s,
                systolic=fmean(x.systolic for x in rs),
                diastolic=fmean(x.diastolic for x in rs),
                n_readings=len(rs),
                reading_ids=tuple(x.id for x in rs),
                first_at=rs[0].measured_at,
            )
        )
    return out


def day_means(occasions: Iterable[Occasion]) -> dict[date, tuple[float, float]]:
    """Day value = mean of the morning and evening occasions present that day."""
    by_day: dict[date, list[Occasion]] = {}
    for o in occasions:
        by_day.setdefault(o.day, []).append(o)
    return {
        d: (fmean(o.systolic for o in os_), fmean(o.diastolic for o in os_))
        for d, os_ in sorted(by_day.items())
    }


@dataclass(frozen=True)
class BpWindow:
    """7-day home BP summary. Means are ``None`` when the minimum day count
    is not met (``n_days`` < 5); morning/evening means likewise need ≥5 days
    with that session."""

    n_days: int
    systolic_mean: float | None
    diastolic_mean: float | None
    morning_sys: float | None
    morning_dia: float | None
    evening_sys: float | None
    evening_dia: float | None
    n_valid: int
    n_invalid: int
    max_reading: BpReadingIn | None
    start: date | None = None
    end: date | None = None
    n_morning_days: int = 0
    n_evening_days: int = 0
    n_irregular_pulse_flags: int = 0
    occasions: tuple[Occasion, ...] = field(default=())

    @property
    def sufficient(self) -> bool:
        return self.systolic_mean is not None

    @property
    def ref(self) -> str:
        return f"bpw_{self.start}_{self.end}"


def _mean_or_none(values: list[float], min_n: int) -> float | None:
    return fmean(values) if len(values) >= min_n else None


def bp_window(
    readings: Iterable[BpReadingIn],
    as_of: datetime,
    days: int = WINDOW_DAYS,
    min_days: int = MIN_WINDOW_DAYS,
) -> BpWindow:
    """Summarise the ``days`` calendar days ending on ``as_of.date()`` (inclusive)."""
    end = as_of.date()
    start = end - timedelta(days=days - 1)
    in_win = [r for r in readings if start <= r.measured_at.date() <= end and r.measured_at <= as_of]
    valid = [r for r in in_win if is_valid_reading(r)]
    occ = occasion_means(in_win)
    dm = day_means(occ)
    morning = [o for o in occ if o.session == "morning"]
    evening = [o for o in occ if o.session == "evening"]
    max_r = max(valid, key=lambda r: (r.systolic, r.diastolic), default=None)
    return BpWindow(
        n_days=len(dm),
        systolic_mean=_mean_or_none([v[0] for v in dm.values()], min_days),
        diastolic_mean=_mean_or_none([v[1] for v in dm.values()], min_days),
        morning_sys=_mean_or_none([o.systolic for o in morning], min_days),
        morning_dia=_mean_or_none([o.diastolic for o in morning], min_days),
        evening_sys=_mean_or_none([o.systolic for o in evening], min_days),
        evening_dia=_mean_or_none([o.diastolic for o in evening], min_days),
        n_valid=len(valid),
        n_invalid=len(in_win) - len(valid),
        max_reading=max_r,
        start=start,
        end=end,
        n_morning_days=len(morning),
        n_evening_days=len(evening),
        n_irregular_pulse_flags=sum(1 for r in in_win if r.irregular_pulse_flag),
        occasions=tuple(occ),
    )


def classify_home_bp(systolic: float, diastolic: float) -> str:
    """Classify a home BP mean.

    While ``HOME_CLASSIFICATION_TABLE_VERIFIED`` is False (default) only the
    two cutoffs verified from the JSH 2019 text are used, because the full
    Table 2-5 category boundaries (normal/high-normal/elevated/grade I–III)
    have not been checked against the primary JSH 2019/2025 text (spec §2):

    * ``below_threshold``    — <135 and <85
    * ``grade_i_or_above``   — ≥135 or ≥85, but <145 and <90
    * ``grade_ii_or_above``  — ≥145 or ≥90

    When the flag is True, the Table 2-5 categories are returned:
    ``normal | high_normal | elevated | grade_i | grade_ii | grade_iii``
    (and/or rule: the higher of the systolic and diastolic category).
    """
    if not HOME_CLASSIFICATION_TABLE_VERIFIED:
        if systolic >= GRADE_II_SYS or diastolic >= GRADE_II_DIA:
            return "grade_ii_or_above"
        if systolic >= HOME_HTN_SYS or diastolic >= HOME_HTN_DIA:
            return "grade_i_or_above"
        return "below_threshold"
    order = ["normal", "high_normal", "elevated", "grade_i", "grade_ii", "grade_iii"]

    def sys_cat(s: float) -> int:
        if s >= 160:
            return 5
        if s >= 145:
            return 4
        if s >= 135:
            return 3
        if s >= 125:
            return 2
        if s >= 115:
            return 1
        return 0

    def dia_cat(d: float) -> int:
        if d >= 100:
            return 5
        if d >= 90:
            return 4
        if d >= 85:
            return 3
        if d >= 75:
            return 2
        return 0  # Table 2-5: normal and high-normal both <75

    return order[max(sys_cat(systolic), dia_cat(diastolic))]
