"""ORM rows → ``app.ml.context`` dataclasses (spec phase-a §8 "Rule engine contract").

Pure mapping: no rule logic lives here. Every row id is copied into the
dataclass ``id`` field so ``evidence[].ref`` links back to the row (§10.5).

Timezone: all datetimes are converted to the user's local timezone
(``Profile.timezone``, default America/Toronto) before they reach the engine, because
the engine groups by ``datetime.date()`` (see ``app.ml.context`` docstring).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from statistics import fmean, median
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    PATHWAYS, AssessmentTask, BpReading, DailyLog, DeviceRhythmResult, MetricSample, Observation, Profile,
    RiskAssessment, User,
)
from app.ml.context import (
    BpReadingIn, DailySeries, ObservationIn, PathwayContext, PriorAssessment, ProfileIn, RhythmResultIn,
    TaskSessionIn,
)
from app.ml.rules import PD_VOWEL_MODEL_KEY as PD_VOWEL_KEY

DEFAULT_TZ = "America/Toronto"
#: Prior assessments are loaded by time window, not count (review fix 7): frequent event /
#: manual runs must not push older weekly windows out of the persistence calculation.
PRIOR_ASSESSMENTS_WINDOW = timedelta(days=60)
PRIOR_ASSESSMENTS_CAP = 500

#: MetricSample.type (ingestion names) → rule series name, reduced by daily mean (§5.2, §6).
MEAN_SERIES: dict[str, str] = {
    "walking_speed_mps": "walking_speed",
    "step_length_m": "step_length",
    "double_support_pct": "double_support_pct",
    "walking_asymmetry_pct": "walking_asymmetry_pct",
}
SLEEP_METRIC = "sleep_asleep_minutes"  # → sleep_duration (hours per night)
#: finger_tap AssessmentTask.features key → series name (§5.3 / PD-04). Keyboard BRAIN test: dwell =
#: akinesia time (AT30), travel-time CV = incoordination (IS30, MAD-cleaned).
TAP_FEATURES: dict[str, str] = {
    "tap_count": "tap_count",
    "mean_iti_ms": "tap_interval_mean",
    "iti_cv": "tap_interval_cv",
    "dwell_mean_ms": "tap_dwell_mean",
    "travel_time_cv": "tap_travel_cv",
}

# ---------------------------------------------------------------- helpers

def user_zone(profile: Profile | None) -> ZoneInfo:
    try:
        return ZoneInfo((profile.timezone if profile else None) or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    """Naive datetimes are treated as UTC (DB convention), then converted to ``tz``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def age_from_dob(dob: date | None, on: date) -> int | None:
    if dob is None:
        return None
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def ckd_stage_from(egfr: float | None, ckd: bool | None) -> int | None:
    """KDIGO G-stage from eGFR; else 3 if CKD is known, 1 if known absent; None if unknown."""
    if egfr is not None:
        if egfr >= 90:
            return 1
        if egfr >= 60:
            return 2
        if egfr >= 30:
            return 3
        return 4 if egfr >= 15 else 5
    if ckd is None:
        return None
    return 3 if ckd else 1


def _smoker(status: str | None) -> bool | None:
    if status is None:
        return None
    return status == "current"


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


#: Blood-pressure-lowering drug names / classes (generic, lower-case substrings) used to derive the
#: Framingham "SBP if treated" input from ``Profile.medications``. Canadian formulary generics.
ANTIHYPERTENSIVES = (
    "amlodipine", "nifedipine", "felodipine", "diltiazem", "verapamil",                       # CCB
    "ramipril", "perindopril", "lisinopril", "enalapril", "trandolapril", "quinapril", "fosinopril",  # ACEi
    "candesartan", "losartan", "valsartan", "irbesartan", "telmisartan", "olmesartan", "azilsartan",  # ARB
    "hydrochlorothiazide", "chlorthalidone", "indapamide", "furosemide", "spironolactone", "eplerenone",
    "metoprolol", "bisoprolol", "atenolol", "carvedilol", "propranolol", "nadolol", "labetalol",
    "doxazosin", "terazosin", "prazosin", "clonidine", "methyldopa", "hydralazine", "sacubitril",
    "blood pressure", "hypertension", "antihypertensive",
)


def bp_treated_from(medications: list | None, known_hypertension: bool | None) -> bool | None:
    """True if any medication looks blood-pressure-lowering; False if a medication list exists without one
    (or hypertension is known absent); None when unknown (no list, hypertension not ruled out)."""
    if medications is None:
        return False if known_hypertension is False else None
    for m in medications:
        text = " ".join(str(v) for v in (m.values() if isinstance(m, dict) else [m]) if v).lower()
        if any(name in text for name in ANTIHYPERTENSIVES):
            return True
    return False


def premature_cvd_family_history(family_history: dict | None) -> bool | None:
    """``Profile.family_history`` → first-degree relative with premature CVD (CCS 2021); None if unknown."""
    if not family_history:
        return None
    for key in ("premature_cvd", "premature_heart_disease", "cvd_premature"):
        if key in family_history and family_history[key] is not None:
            return bool(family_history[key])
    return None


# ---------------------------------------------------------------- row mappers

def map_profile(p: Profile | None, on: date) -> ProfileIn:
    if p is None:
        return ProfileIn()
    return ProfileIn(
        age=age_from_dob(p.date_of_birth, on),
        sex=p.sex if p.sex in ("male", "female") else None,
        smoker=_smoker(p.smoking_status),
        diabetes=p.diabetes,
        ckd_stage=ckd_stage_from(p.egfr, p.ckd),
        egfr=p.egfr,
        ldl=p.ldl_mg_dl,
        hdl=p.hdl_mg_dl,
        total_chol=p.total_chol_mg_dl,
        bp_treated=bp_treated_from(p.medications, p.known_hypertension),
        family_history_premature_cvd=premature_cvd_family_history(p.family_history),
        known_af=bool(p.known_af),
        known_chd=bool(p.known_chd),
        known_pd=bool(p.known_pd),
        known_dementia=bool(p.known_dementia),
    )


def map_bp(r: BpReading, tz: ZoneInfo) -> BpReadingIn:
    return BpReadingIn(measured_at=to_local(r.measured_at, tz), session=r.session or "other",
                       systolic=r.systolic, diastolic=r.diastolic, pulse=r.pulse,
                       irregular_pulse_flag=bool(r.irregular_pulse_flag), valid=bool(r.valid), id=r.id)


def map_rhythm(r: DeviceRhythmResult, tz: ZoneInfo) -> RhythmResultIn:
    # Prefer the normalised label; the engine re-normalises raw strings anyway.
    return RhythmResultIn(recorded_at=to_local(r.recorded_at, tz), source=r.source,
                          label=r.label or r.raw_label or "other", id=r.id)


def map_observation(o: Observation, tz: ZoneInfo) -> ObservationIn:
    return ObservationIn(created_at=to_local(o.created_at, tz), kind=o.kind, label=o.label,
                         severity=_int_or_none(o.severity), free_text=o.free_text, id=o.id)


def task_device(t: AssessmentTask) -> str | None:
    """Coarse recording-device label stored at upload time (``recording_quality.device``, else
    ``raw_json.device``; see ``task_service.device_label``). ``None`` = unknown."""
    for blob in (t.recording_quality, t.raw_json):
        if isinstance(blob, dict) and isinstance(blob.get("device"), str) and blob["device"].strip():
            return blob["device"].strip()
    return None


def map_task(t: AssessmentTask, tz: ZoneInfo) -> TaskSessionIn:
    return TaskSessionIn(created_at=to_local(t.created_at, tz), task_type=t.task_type, score=t.score,
                         valid=bool(t.valid), model_scores=dict(t.model_scores or {}), id=t.id,
                         features=dict(t.features or {}), device=task_device(t),
                         recording_quality=dict(t.recording_quality or {}))


def _median_features(rows: list[TaskSessionIn]) -> dict[str, Any]:
    """Per-key median of the numeric feature values over a day's repetitions (non-numeric keys: last row)."""
    keys = list(dict.fromkeys(k for r in rows for k in r.features))
    out: dict[str, Any] = {}
    for k in keys:
        vals = [float(r.features[k]) for r in rows if isinstance(r.features.get(k), (int, float))
                and not isinstance(r.features.get(k), bool)]
        out[k] = median(vals) if vals else rows[-1].features.get(k)
    return out


def aggregate_vowel_sessions(tasks: list[TaskSessionIn]) -> list[TaskSessionIn]:
    """Collapse valid ``sustained_vowel`` rows to one session per local calendar day (spec §5.4: a
    session is ~3 repetitions): the model probability is the median via ``aggregate_sessions`` so
    PD-03's "≥2 sessions ≥7 days apart" counts days, not repetitions, and the acoustic ``features``
    are the per-key median over the repetitions (PD-03B, §5.5). The day's session keeps the latest
    row's id/time, device and model metadata, plus ``repetitions`` and ``task_ids`` on the model
    score. Rows with neither a model score nor features, and invalid rows, are returned unchanged;
    output is ordered by ``created_at``."""
    from app.ml.pd_vowel_model import aggregate_sessions

    by_day: dict[date, list[TaskSessionIn]] = defaultdict(list)
    out: list[TaskSessionIn] = []
    for t in tasks:
        m = t.model_scores.get(PD_VOWEL_KEY) if t.task_type == "sustained_vowel" and t.valid else None
        scored = isinstance(m, dict) and m.get("probability") is not None
        if t.task_type == "sustained_vowel" and t.valid and (scored or t.features):
            by_day[t.created_at.date()].append(t)
        else:
            out.append(t)
    for day_rows in by_day.values():
        day_rows.sort(key=lambda r: r.created_at)
        last = day_rows[-1]
        scored_rows = [r for r in day_rows if isinstance(r.model_scores.get(PD_VOWEL_KEY), dict)
                       and r.model_scores[PD_VOWEL_KEY].get("probability") is not None]
        model_scores = dict(last.model_scores)
        score = last.score
        if scored_rows:
            merged = dict(scored_rows[-1].model_scores[PD_VOWEL_KEY])
            merged["probability"] = aggregate_sessions(r.model_scores[PD_VOWEL_KEY]["probability"] for r in scored_rows)
            merged["repetitions"] = len(scored_rows)
            merged["task_ids"] = [r.id for r in scored_rows]
            model_scores[PD_VOWEL_KEY] = merged
            score = merged["probability"]
        featured = [r for r in day_rows if r.features]
        out.append(replace(last, score=score, model_scores=model_scores,
                           features=_median_features(featured) if featured else dict(last.features)))
    return sorted(out, key=lambda r: r.created_at)


def map_prior(a: RiskAssessment, tz: ZoneInfo) -> PriorAssessment:
    return PriorAssessment(created_at=to_local(a.created_at, tz), level=a.level,
                           fired_rules=list(a.fired_rules or []))


# ---------------------------------------------------------------- series

def _series(name: str, by_day: dict[date, float]) -> DailySeries:
    return DailySeries(name, sorted(by_day.items()))


def metric_series(samples: Iterable[MetricSample], tz: ZoneInfo) -> dict[str, DailySeries]:
    """Daily aggregates of MetricSample rows mapped to rule series names."""
    means: dict[str, dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
    sleep: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for s in samples:
        d = to_local(s.recorded_at, tz).date()
        if s.type in MEAN_SERIES:
            means[MEAN_SERIES[s.type]][d].append(float(s.value))
        elif s.type == SLEEP_METRIC:
            # Sum segments per night per source; take the largest source total (avoid double counting).
            sleep[d][s.source or "?"] += float(s.value)
    out = {name: _series(name, {d: fmean(v) for d, v in days.items()}) for name, days in means.items()}
    if sleep:
        out["sleep_duration"] = _series("sleep_duration", {d: max(v.values()) / 60.0 for d, v in sleep.items()})
    return out


def fatigue_series(logs: Iterable[DailyLog]) -> DailySeries | None:
    pts = {log.date: float(log.fatigue) for log in logs if log.fatigue is not None}
    return _series("fatigue", pts) if pts else None


def _tap_values(features: dict[str, Any] | None) -> dict[str, float]:
    """Flat ``{tap_count, mean_iti_ms, iti_cv}`` or per-hand ``{"left": {...}, "right": {...}}`` (averaged)."""
    if not features:
        return {}
    flat = {k: features.get(k) for k in TAP_FEATURES if isinstance(features.get(k), (int, float))}
    if flat:
        return {k: float(v) for k, v in flat.items()}
    nested = [v for v in features.values() if isinstance(v, dict)]
    out: dict[str, float] = {}
    for k in TAP_FEATURES:
        vals = [float(h[k]) for h in nested if isinstance(h.get(k), (int, float))]
        if vals:
            out[k] = fmean(vals)
    return out


def tapping_series(tasks: Iterable[AssessmentTask], tz: ZoneInfo) -> dict[str, DailySeries]:
    """PD-04 daily series from valid ``finger_tap`` tasks. Like PD-03B (§5.5), only sessions on the input
    method / device of the latest valid session count (``task_device``: "keyboard"); an earlier device's
    sessions are dropped so a device change restarts the baseline."""
    valid = sorted((t for t in tasks if t.task_type == "finger_tap" and t.valid), key=lambda t: t.created_at)
    if not valid:
        return {}
    device = task_device(valid[-1])
    per: dict[str, dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
    for t in valid:
        if task_device(t) != device:
            continue
        d = to_local(t.created_at, tz).date()
        for k, v in _tap_values(t.features).items():
            per[TAP_FEATURES[k]][d].append(v)
    return {name: _series(name, {d: fmean(v) for d, v in days.items()}) for name, days in per.items()}


# ---------------------------------------------------------------- main entry

def _rows(db: Session, model, user_id: str, col, since: datetime, until: datetime) -> list:
    return list(db.scalars(select(model).where(model.user_id == user_id, col >= since, col <= until).order_by(col)))


def build_contexts(db: Session, user: User, as_of: datetime | None = None,
                   lookback_days: int = 120) -> dict[str, PathwayContext]:
    """One ``PathwayContext`` per pathway (``PATHWAYS`` order) for ``user`` at ``as_of``.

    ``as_of`` defaults to now; it is returned in the user's local timezone. Rows
    after ``as_of`` or older than ``lookback_days`` are excluded.
    """
    profile = user.profile
    tz = user_zone(profile)
    as_of_local = to_local(as_of or datetime.now(timezone.utc), tz)
    until = as_of_local.astimezone(timezone.utc)
    since = until - timedelta(days=lookback_days)
    uid = user.id

    prof = map_profile(profile, as_of_local.date())
    bp = [map_bp(r, tz) for r in _rows(db, BpReading, uid, BpReading.measured_at, since, until)]
    rhythm = [map_rhythm(r, tz) for r in _rows(db, DeviceRhythmResult, uid, DeviceRhythmResult.recorded_at, since, until)]
    obs = [map_observation(o, tz) for o in _rows(db, Observation, uid, Observation.created_at, since, until)]
    task_rows = _rows(db, AssessmentTask, uid, AssessmentTask.created_at, since, until)
    tasks = aggregate_vowel_sessions([map_task(t, tz) for t in task_rows])

    series = metric_series(_rows(db, MetricSample, uid, MetricSample.recorded_at, since, until), tz)
    series.update(tapping_series(task_rows, tz))
    logs = list(db.scalars(select(DailyLog).where(DailyLog.user_id == uid, DailyLog.date >= since.astimezone(tz).date(),
                                                  DailyLog.date <= as_of_local.date())))
    if (fat := fatigue_series(logs)) is not None:
        series["fatigue"] = fat

    contexts: dict[str, PathwayContext] = {}
    for pathway in PATHWAYS:
        priors = list(db.scalars(
            select(RiskAssessment).where(RiskAssessment.user_id == uid, RiskAssessment.pathway == pathway,
                                         RiskAssessment.created_at <= until,
                                         RiskAssessment.created_at >= until - PRIOR_ASSESSMENTS_WINDOW)
            .order_by(RiskAssessment.created_at.desc()).limit(PRIOR_ASSESSMENTS_CAP)))
        contexts[pathway] = PathwayContext(
            user_id=uid, pathway=pathway, as_of=as_of_local, profile=prof, region=settings.default_country,
            bp_readings=bp,
            rhythm_results=rhythm, observations=obs, series=dict(series), task_sessions=tasks,
            prior_assessments=[map_prior(a, tz) for a in reversed(priors)],
        )
    return contexts
