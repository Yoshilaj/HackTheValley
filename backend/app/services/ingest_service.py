"""Health-data ingestion (BACKEND_PLAN C1; spec phase-a-assessments §2, §4, §7, §1.3 COG-03).

All functions take a Session and flush but do not commit — the caller (route / job) commits.
Naive datetimes are interpreted in the user's profile timezone (default America/Toronto).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    BP_SESSIONS,
    METRIC_SOURCES,
    RHYTHM_LABELS,
    RHYTHM_SOURCES,
    BpReading,
    DailyLog,
    DeviceRhythmResult,
    MetricSample,
    Observation,
    Profile,
)
from app.ml.bp import DIA_MAX, DIA_MIN, PULSE_MAX, PULSE_MIN, SYS_MAX, SYS_MIN  # single source of the BP ranges
from app.schemas.metrics import METRIC_TYPES, BpIn, DailyLogIn, MetricIn

DEFAULT_TZ = "America/Toronto"

# ---------------------------------------------------------------- time helpers


def user_tz(db: Session, user_id: str) -> ZoneInfo:
    name = db.scalar(select(Profile.timezone).where(Profile.user_id == user_id)) or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def as_aware(dt: datetime, tz: ZoneInfo | timezone) -> datetime:
    """Attach `tz` to naive datetimes; normalise to UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def local_date(dt: datetime, tz: ZoneInfo) -> date:
    return as_aware(dt, timezone.utc).astimezone(tz).date()


def day_bounds_utc(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def session_for_hour(hour: int) -> str:
    """Spec §2 occasion by local hour: 04–11 morning, 17–23 evening, else other."""
    if 4 <= hour < 12:
        return "morning"
    if 17 <= hour < 24:
        return "evening"
    return "other"


# ================================================================ metrics

@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    duplicates: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"inserted": self.inserted, "updated": self.updated, "duplicates": self.duplicates,
                "rejected": self.rejected}


def validate_metric(sample: MetricIn, source: str) -> str | None:
    """Return a rejection reason or None (METRIC_TYPES registry + METRIC_SOURCES)."""
    mt = METRIC_TYPES.get(sample.type)
    if mt is None:
        return f"unknown_type:{sample.type}"
    if source not in METRIC_SOURCES:
        return f"unknown_source:{source}"
    if sample.value != sample.value:  # NaN
        return "value_not_a_number"
    if not (mt.min <= sample.value <= mt.max):
        return f"out_of_range:{sample.type} must be within [{mt.min}, {mt.max}] {mt.unit}"
    if sample.unit and sample.unit != mt.unit:
        return f"unit_mismatch:{sample.type} expects {mt.unit}"
    return None


def upsert_metrics(db: Session, user_id: str, samples: list[MetricIn],
                   default_source: str = "manual") -> UpsertResult:
    """Idempotent on (user_id, type, recorded_at, source). Same key + same value → duplicate;
    same key + new value → updated (e.g. the Shortcut re-posting today's running step count).
    Invalid samples are rejected individually ({index, reason}); the rest are stored."""
    res = UpsertResult()
    tz = user_tz(db, user_id)
    valid: list[tuple[int, MetricIn, str, datetime]] = []
    for i, s in enumerate(samples):
        source = s.source or default_source
        reason = validate_metric(s, source)
        if reason:
            res.rejected.append({"index": i, "reason": reason})
            continue
        valid.append((i, s, source, as_aware(s.recorded_at, tz)))
    if not valid:
        return res

    types = {s.type for _, s, _, _ in valid}
    lo = min(t for *_, t in valid)
    hi = max(t for *_, t in valid)
    existing: dict[tuple[str, datetime, str], MetricSample] = {
        (row.type, as_aware(row.recorded_at, timezone.utc), row.source): row
        for row in db.scalars(select(MetricSample).where(
            MetricSample.user_id == user_id, MetricSample.type.in_(types),
            MetricSample.recorded_at >= lo, MetricSample.recorded_at <= hi,
        ))
    }
    for _, s, source, ts in valid:
        key = (s.type, ts, source)
        row = existing.get(key)
        if row is not None:
            if abs(row.value - s.value) < 1e-9:
                res.duplicates += 1
            else:
                row.value = s.value
                row.meta = s.meta if s.meta is not None else row.meta
                res.updated += 1
            continue
        row = MetricSample(user_id=user_id, type=s.type, value=float(s.value),
                           unit=s.unit or METRIC_TYPES[s.type].unit, recorded_at=ts, source=source, meta=s.meta)
        db.add(row)
        existing[key] = row
        res.inserted += 1
    db.flush()
    return res


# ================================================================ blood pressure

def bp_validity(r: BpIn) -> tuple[bool, str | None]:
    """Spec §2 'Validity' (ranges and reason strings match app/ml/bp.reading_validity)."""
    if r.user_marked_error:
        return False, "user_marked_error"
    if r.systolic <= r.diastolic:
        return False, "systolic_not_above_diastolic"
    if r.systolic < SYS_MIN or r.systolic > SYS_MAX:
        return False, "systolic_out_of_range"
    if r.diastolic < DIA_MIN or r.diastolic > DIA_MAX:
        return False, "diastolic_out_of_range"
    if r.pulse is not None and (r.pulse < PULSE_MIN or r.pulse > PULSE_MAX):
        return False, "pulse_out_of_range"
    return True, None


def add_bp_readings(db: Session, user_id: str, readings: list[BpIn]) -> list[BpReading]:
    """Store readings with validity flags (every reading is recorded, JSH 2019).
    `reading_no` auto-increments within the same session and local day.
    An identical reading (same time, values, source) is not stored twice.
    `irregular_pulse_flag` → also a DeviceRhythmResult(source='manual_irregular_pulse',
    label='irregular_rhythm_notification') so RHY-01 sees it (spec §4).
    Returns rows in input order."""
    tz = user_tz(db, user_id)
    counters: dict[tuple[date, str], int] = {}
    out: dict[int, BpReading] = {}
    order = sorted(range(len(readings)), key=lambda i: as_aware(readings[i].measured_at, tz))
    for idx in order:
        r = readings[idx]
        ts = as_aware(r.measured_at, tz)
        dup = db.scalar(select(BpReading).where(
            BpReading.user_id == user_id, BpReading.measured_at == ts, BpReading.systolic == r.systolic,
            BpReading.diastolic == r.diastolic, BpReading.source == (r.source or "manual")))
        if dup is not None:  # idempotent re-submission / re-import
            out[idx] = dup
            continue
        local = ts.astimezone(tz)
        session = r.session or session_for_hour(local.hour)
        if session not in BP_SESSIONS:
            session = "other"
        key = (local.date(), session)
        if key not in counters:
            lo, hi = day_bounds_utc(local.date(), tz)
            counters[key] = max(
                [row.reading_no for row in db.scalars(select(BpReading).where(
                    BpReading.user_id == user_id, BpReading.session == session,
                    BpReading.measured_at >= lo, BpReading.measured_at < hi))] or [0]
            )
        reading_no = r.reading_no or counters[key] + 1
        counters[key] = max(counters[key], reading_no)
        valid, reason = bp_validity(r)
        row = BpReading(
            user_id=user_id, session=session, reading_no=reading_no, systolic=r.systolic,
            diastolic=r.diastolic, pulse=r.pulse, irregular_pulse_flag=r.irregular_pulse_flag,
            measured_at=ts, arm=r.arm, position=r.position, pre_medication=r.pre_medication,
            valid=valid, invalid_reason=reason, source=r.source or "manual",
        )
        db.add(row)
        db.flush()
        if r.irregular_pulse_flag:
            db.add(DeviceRhythmResult(
                user_id=user_id, source="manual_irregular_pulse", label="irregular_rhythm_notification",
                raw_label="cuff_irregular_pulse_indicator", recorded_at=ts,
                meta={"bp_reading_id": row.id, "bp_source": row.source},
            ))
        out[idx] = row
    db.flush()
    return [out[i] for i in range(len(readings))]


# ================================================================ rhythm results

_APPLE_ECG = {
    "sinus rhythm": "sinus_rhythm", "洞調律": "sinus_rhythm",
    "atrial fibrillation": "afib", "afib": "afib", "心房細動": "afib",
    "high heart rate": "high_hr", "high hr": "high_hr", "心拍数が高い": "high_hr", "高心拍数": "high_hr",
    "low heart rate": "low_hr", "low hr": "low_hr", "心拍数が低い": "low_hr", "低心拍数": "low_hr",
    "inconclusive": "inconclusive", "判定不能": "inconclusive", "unrecognized": "inconclusive",
    "poor recording": "poor_recording", "記録不良": "poor_recording",
    # Apple Health export classification identifiers
    "hkelectrocardiogramclassificationsinusrhythm": "sinus_rhythm",
    "hkelectrocardiogramclassificationatrialfibrillation": "afib",
    "hkelectrocardiogramclassificationinconclusivehighheartrate": "high_hr",
    "hkelectrocardiogramclassificationinconclusivelowheartrate": "low_hr",
    "hkelectrocardiogramclassificationinconclusiveother": "inconclusive",
    "hkelectrocardiogramclassificationinconclusivepoorreading": "poor_recording",
    "hkelectrocardiogramclassificationunrecognized": "inconclusive",
}
_APPLE_EVENTS = {
    "hkcategorytypeidentifierirregularheartrhythmevent": "irregular_rhythm_notification",
    "irregular rhythm": "irregular_rhythm_notification",
    "irregular rhythm notification": "irregular_rhythm_notification",
    "不規則な心拍の通知": "irregular_rhythm_notification",
    "hkcategorytypeidentifierhighheartrateevent": "high_hr",
    "hkcategorytypeidentifierlowheartrateevent": "low_hr",
}
_OMRON = {
    "心房細動の可能性": "possible_af", "心房細動の可能性あり": "possible_af",
    "possible afib": "possible_af", "possible atrial fibrillation": "possible_af",
    "正常": "sinus_rhythm", "正常範囲内": "sinus_rhythm", "normal": "sinus_rhythm",
    "徐脈の可能性": "low_hr", "頻脈の可能性": "high_hr",
    "判定できません": "inconclusive", "判定不能": "inconclusive",
    # Remaining OMRON connect patterns: add once seen on a real PDF (raw_label is always kept).
}


def normalize_rhythm_label(source: str, raw_label: str) -> str:
    """Map a device label to RHYTHM_LABELS (spec §4); unknown → 'other'."""
    raw = (raw_label or "").strip()
    if raw in RHYTHM_LABELS:
        return raw
    low = raw.lower()
    if low in _APPLE_EVENTS:
        return _APPLE_EVENTS[low]
    if source == "omron_connect":
        return _OMRON.get(raw) or _OMRON.get(low) or "other"
    if source == "apple_ecg":
        return _APPLE_ECG.get(low) or _APPLE_ECG.get(raw) or "other"
    if source in ("apple_irn", "manual_irregular_pulse"):
        return "irregular_rhythm_notification"
    return _APPLE_ECG.get(low) or _OMRON.get(raw) or "other"


def add_rhythm_result(db: Session, user_id: str, source: str, raw_label: str, recorded_at: datetime,
                      file_url: str | None = None, meta: dict[str, Any] | None = None) -> DeviceRhythmResult:
    """Store a device rhythm result (spec §4) with the normalised label and the raw string.
    Idempotent on (user, source, recorded_at, raw_label)."""
    if source not in RHYTHM_SOURCES:
        raise ValueError(f"unknown rhythm source {source!r}; expected one of {RHYTHM_SOURCES}")
    ts = as_aware(recorded_at, user_tz(db, user_id))
    existing = db.scalar(select(DeviceRhythmResult).where(
        DeviceRhythmResult.user_id == user_id, DeviceRhythmResult.source == source,
        DeviceRhythmResult.recorded_at == ts, DeviceRhythmResult.raw_label == raw_label))
    if existing:
        if file_url and not existing.file_url:
            existing.file_url = file_url
        return existing
    row = DeviceRhythmResult(user_id=user_id, source=source, label=normalize_rhythm_label(source, raw_label),
                             raw_label=raw_label, recorded_at=ts, file_url=file_url, meta=meta)
    db.add(row)
    db.flush()
    return row


# ================================================================ daily log → observations

#: flag → [(kind, label)]. Red-flag labels use app/ml/rules.py RED_FLAG_LABELS vocabulary
#: (spec §7 EMG-01); functional changes feed COG-03 (spec §1.3).
FLAG_OBSERVATIONS: dict[str, list[tuple[str, str]]] = {
    "chest_discomfort": [("red_flag", "chest_pressure")],
    "chest_pain": [("red_flag", "chest_pain")],
    "vision_speech_disturbance": [("red_flag", "speech_difficulty"), ("red_flag", "vision_disturbance")],
    "speech_disturbance": [("red_flag", "speech_difficulty")],
    "vision_disturbance": [("red_flag", "vision_disturbance")],
    "fainting": [("red_flag", "fainting")],
    "face_droop": [("red_flag", "face_droop")],
    "arm_weakness": [("red_flag", "arm_weakness")],
    "severe_headache": [("red_flag", "severe_sudden_headache")],
    "severe_sudden_headache": [("red_flag", "severe_sudden_headache")],
    "suicidal_ideation": [("red_flag", "suicidal_ideation")],
    "memory_lapse": [("functional_change", "memory_lapse")],
    "confusion": [("functional_change", "confusion")],
    "getting_lost": [("functional_change", "getting_lost")],
    "repeating_questions": [("functional_change", "repeating_questions")],
    "missed_appointments": [("functional_change", "missed_appointments")],
    "trouble_managing_money": [("functional_change", "trouble_managing_money")],
    "trouble_managing_medication": [("functional_change", "trouble_managing_medication")],
}
# Everything else that is true (tremor, stiffness, fall, headache, dizziness, palpitations,
# breathlessness, ...) → ("symptom", flag).


def flag_to_observations(flag: str) -> list[tuple[str, str]]:
    return FLAG_OBSERVATIONS.get(flag, [("symptom", flag)])


_LOG_FIELDS = ("sleep_quality", "fatigue", "mood", "stress", "flags", "medication_adherence",
               "caffeine_alcohol", "exercise_minutes", "notes")


def add_daily_log(db: Session, user_id: str, log: DailyLogIn) -> tuple[DailyLog, list[Observation]]:
    """Upsert the check-in for `log.date`; re-derive its Observations (source='log',
    source_id=DailyLog.id) from the true flags so re-submitting is idempotent."""
    row = db.scalar(select(DailyLog).where(DailyLog.user_id == user_id, DailyLog.date == log.date))
    data = log.model_dump(exclude_unset=True)
    if row is None:
        row = DailyLog(user_id=user_id, date=log.date)
        db.add(row)
    for f in _LOG_FIELDS:
        if f in data:
            setattr(row, f, data[f])
    db.flush()

    # Upsert by (kind, label): keep rows (and their original created_at — the EMG-01 24 h window
    # must not be extended by re-posting) for flags that are still true; delete the rest.
    existing = {(o.kind, o.label): o for o in log_observations(db, user_id, row.id)}
    obs: list[Observation] = []
    seen: set[tuple[str, str]] = set()
    for flag, value in sorted((row.flags or {}).items()):
        if not value:
            continue
        for kind, label in flag_to_observations(flag):
            if (kind, label) in seen:
                continue
            seen.add((kind, label))
            o = existing.get((kind, label))
            if o is None:
                o = Observation(user_id=user_id, kind=kind, label=label, onset=log.date.isoformat(),
                                free_text=f"daily log flag: {flag}", source="log", source_id=row.id,
                                severity="urgent" if kind == "red_flag" else None)
                db.add(o)
            obs.append(o)
    for key, o in existing.items():
        # functional_change_reported (check-in free text) is not flag-derived → keep it
        if key not in seen and key != ("functional_change", "functional_change_reported"):
            db.delete(o)
    db.flush()
    return row, obs


def log_observations(db: Session, user_id: str, log_id: str) -> list[Observation]:
    return list(db.scalars(select(Observation).where(
        Observation.user_id == user_id, Observation.source == "log", Observation.source_id == log_id)))


# ================================================================ summaries

def _median_mad(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    med = statistics.median(values)
    return {"median": med, "mad": statistics.median(abs(v - med) for v in values), "n_days": len(values)}


def metrics_summary(db: Session, user_id: str, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    """Per metric type: daily aggregates (sum or mean per METRIC_TYPES), 30-day baseline
    {median, mad} over daily values, last value and number of days with data.
    Multiple sources on one day: cumulative types take the max per-source sum (avoids
    iPhone+Watch double counting), level types average all samples."""
    tz = user_tz(db, user_id)
    now = as_aware(now or datetime.now(timezone.utc), timezone.utc)
    today = now.astimezone(tz).date()
    span = max(days, 30)
    start_utc, _ = day_bounds_utc(today - timedelta(days=span - 1), tz)
    rows = db.scalars(select(MetricSample).where(
        MetricSample.user_id == user_id, MetricSample.recorded_at >= start_utc,
        MetricSample.recorded_at <= now).order_by(MetricSample.recorded_at))

    per: dict[str, dict[date, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    last: dict[str, MetricSample] = {}
    for r in rows:
        per[r.type][local_date(r.recorded_at, tz)][r.source].append(r.value)
        last[r.type] = r

    out: dict[str, Any] = {}
    for mtype, by_day in sorted(per.items()):
        reg = METRIC_TYPES.get(mtype)
        agg = reg.agg if reg else "mean"
        daily_all: dict[date, float] = {}
        for d, by_src in by_day.items():
            if agg == "sum":
                daily_all[d] = max(sum(v) for v in by_src.values())
            else:
                vals = [x for v in by_src.values() for x in v]
                daily_all[d] = statistics.fmean(vals)
        window_start = today - timedelta(days=days - 1)
        base_start = today - timedelta(days=29)
        daily = [{"date": d.isoformat(), "value": round(v, 3)} for d, v in sorted(daily_all.items())
                 if d >= window_start]
        lr = last[mtype]
        out[mtype] = {
            "unit": reg.unit if reg else lr.unit,
            "aggregation": agg,
            "daily": daily,
            "baseline_30d": _median_mad([v for d, v in daily_all.items() if d >= base_start]),
            "last_value": {"value": lr.value, "recorded_at": as_aware(lr.recorded_at, timezone.utc).isoformat(),
                           "source": lr.source},
            "n_days": len(daily),
        }
    return {"days": days, "as_of": now.isoformat(), "metrics": out,
            "bp": bp_summary(db, user_id, days=min(days, 14), now=now)}


def bp_summary(db: Session, user_id: str, days: int = 14, now: datetime | None = None) -> dict[str, Any]:
    """Spec §2 means: occasion = mean of valid readings of a session (first 3), day = mean of the
    morning and evening occasions, 7-day = mean of day values (sufficient when ≥5 days).
    Uses app.ml.bp.bp_window for the 7-day window when available; otherwise simple means."""
    tz = user_tz(db, user_id)
    now = as_aware(now or datetime.now(timezone.utc), timezone.utc)
    today = now.astimezone(tz).date()
    start_utc, _ = day_bounds_utc(today - timedelta(days=max(days, 7) - 1), tz)
    rows = list(db.scalars(select(BpReading).where(
        BpReading.user_id == user_id, BpReading.measured_at >= start_utc, BpReading.measured_at <= now)
        .order_by(BpReading.measured_at)))

    occ: dict[tuple[date, str], list[BpReading]] = defaultdict(list)
    for r in rows:
        if r.valid and r.session in ("morning", "evening"):
            occ[(local_date(r.measured_at, tz), r.session)].append(r)

    def mean_pair(rs: list[BpReading]) -> dict[str, Any]:
        rs = rs[:3]
        return {"systolic": round(statistics.fmean(x.systolic for x in rs), 1),
                "diastolic": round(statistics.fmean(x.diastolic for x in rs), 1), "n": len(rs)}

    per_day: dict[date, dict[str, Any]] = {}
    for (d, s), rs in sorted(occ.items()):
        per_day.setdefault(d, {"date": d.isoformat()})[s] = mean_pair(rs)
    for d, entry in per_day.items():
        sessions = [entry[s] for s in ("morning", "evening") if s in entry]
        entry["day_mean"] = {"systolic": round(statistics.fmean(x["systolic"] for x in sessions), 1),
                             "diastolic": round(statistics.fmean(x["diastolic"] for x in sessions), 1)}

    win_start = today - timedelta(days=6)
    win = [e for d, e in per_day.items() if d >= win_start]

    def wmean(key: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        xs = [e[key] for e in entries if key in e]
        if not xs:
            return None
        return {"systolic": round(statistics.fmean(x["systolic"] for x in xs), 1),
                "diastolic": round(statistics.fmean(x["diastolic"] for x in xs), 1), "n_days": len(xs),
                "sufficient": len(xs) >= 5}

    window = {"start": win_start.isoformat(), "end": today.isoformat(),
              "day_mean": wmean("day_mean", win), "morning": wmean("morning", win),
              "evening": wmean("evening", win), "method": "simple"}
    try:  # lazy: JSH window logic owned by app/ml/bp.py
        from app.ml import bp as ml_bp
        from app.ml.context import BpReadingIn

        ins = [BpReadingIn(measured_at=r.measured_at.astimezone(tz).replace(tzinfo=None), session=r.session,
                           systolic=r.systolic, diastolic=r.diastolic, pulse=r.pulse,
                           irregular_pulse_flag=r.irregular_pulse_flag, valid=r.valid, id=r.id) for r in rows]
        w = ml_bp.bp_window(ins, as_of=now.astimezone(tz).replace(tzinfo=None))
        window["jsh"] = {"n_days": w.n_days, "systolic_mean": w.systolic_mean, "diastolic_mean": w.diastolic_mean,
                         "morning_sys": w.morning_sys, "morning_dia": w.morning_dia,
                         "evening_sys": w.evening_sys, "evening_dia": w.evening_dia, "sufficient": w.sufficient}
        window["method"] = "app.ml.bp"
    except Exception:  # module missing or incompatible → simple means only
        pass

    return {
        "days": days,
        "per_day": [e for d, e in sorted(per_day.items()) if d >= today - timedelta(days=days - 1)],
        "window_7d": window,
        "n_readings": len(rows),
        "n_invalid": sum(1 for r in rows if not r.valid),
        "n_irregular_pulse_flags": sum(1 for r in rows if r.irregular_pulse_flag),
    }
