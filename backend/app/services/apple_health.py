"""Apple Health export parser (BACKEND_PLAN C1 / A5 §1; spec §4 device rhythm results).

`parse_apple_health_export` streams `export.xml` with `ElementTree.iterparse` (elements are
cleared as they are consumed, so memory stays flat on multi-GB exports) and returns daily
`MetricIn` samples (source 'apple_health'), BP pairs, rhythm events and ECG CSVs.
`import_apple_health` persists a parse result through ingest_service + storage.

Aggregation: quantity samples → one value per local day (the day of `startDate` in the
offset written by the phone). Cumulative types (steps, distance, energy, exercise) are
summed per source device and the largest per-source total is used (iPhone and Watch both
record steps; summing both would double count). Level types are averaged over all samples.
Sleep: minutes per night, night = wake date = local date of (endDate + 6 h).
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import fmean
from typing import IO, Any, Callable
from xml.etree.ElementTree import iterparse

from sqlalchemy.orm import Session

from app.schemas.metrics import METRIC_TYPES, BpIn, MetricIn
from app.services.ingest_service import (
    add_bp_readings,
    add_rhythm_result,
    normalize_rhythm_label,
    session_for_hour,
    upsert_metrics,
)

SOURCE = "apple_health"
HK_DATE_FMT = "%Y-%m-%d %H:%M:%S %z"


def _conv(factor: float) -> Callable[[float], float]:
    return lambda v: v * factor


# HK type → (metric type, {unit: converter}); None key = any unit, identity.
_ID = {None: lambda v: v}
QUANTITY_MAP: dict[str, tuple[str, dict[str | None, Callable[[float], float]]]] = {
    "HKQuantityTypeIdentifierStepCount": ("steps", _ID),
    "HKQuantityTypeIdentifierDistanceWalkingRunning": ("distance_m", {"km": _conv(1000), "m": _ID[None],
                                                                     "mi": _conv(1609.344), None: _ID[None]}),
    "HKQuantityTypeIdentifierActiveEnergyBurned": ("active_energy_kcal", {"kJ": _conv(1 / 4.184), None: _ID[None]}),
    "HKQuantityTypeIdentifierAppleExerciseTime": ("exercise_minutes", _ID),
    "HKQuantityTypeIdentifierRestingHeartRate": ("resting_hr", _ID),
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": ("walking_hr_avg", _ID),
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("hrv_sdnn_ms", _ID),
    "HKQuantityTypeIdentifierOxygenSaturation": ("spo2_pct", {"%": lambda v: v * 100 if v <= 1.0 else v,
                                                              None: lambda v: v * 100 if v <= 1.0 else v}),
    "HKQuantityTypeIdentifierRespiratoryRate": ("respiratory_rate", _ID),
    "HKQuantityTypeIdentifierVO2Max": ("vo2max", _ID),
    "HKQuantityTypeIdentifierWalkingSpeed": ("walking_speed_mps", {"km/hr": _conv(1 / 3.6), "m/s": _ID[None],
                                                                   "mi/hr": _conv(0.44704), None: _ID[None]}),
    "HKQuantityTypeIdentifierWalkingStepLength": ("step_length_m", {"cm": _conv(0.01), "m": _ID[None],
                                                                    "in": _conv(0.0254), "ft": _conv(0.3048),
                                                                    None: _ID[None]}),
    "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage": ("double_support_pct",
                                                               {None: lambda v: v * 100 if v <= 1.0 else v}),
    "HKQuantityTypeIdentifierWalkingAsymmetryPercentage": ("walking_asymmetry_pct",
                                                           {None: lambda v: v * 100 if v <= 1.0 else v}),
    "HKQuantityTypeIdentifierAppleWalkingSteadiness": ("walking_steadiness",
                                                       {None: lambda v: v * 100 if v <= 1.0 else v}),
    "HKQuantityTypeIdentifierBodyMass": ("weight_kg", {"lb": _conv(0.45359237), "g": _conv(0.001), None: _ID[None]}),
    "HKQuantityTypeIdentifierBodyMassIndex": ("bmi", _ID),
    "HKQuantityTypeIdentifierBodyTemperature": ("body_temp_c", {"degF": lambda v: (v - 32) / 1.8, None: _ID[None]}),
}
BP_SYS = "HKQuantityTypeIdentifierBloodPressureSystolic"
BP_DIA = "HKQuantityTypeIdentifierBloodPressureDiastolic"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
RHYTHM_EVENTS = {
    "HKCategoryTypeIdentifierIrregularHeartRhythmEvent": "irregular_rhythm_notification",
    "HKCategoryTypeIdentifierHighHeartRateEvent": "high_hr",
    "HKCategoryTypeIdentifierLowHeartRateEvent": "low_hr",
}
#: sleep category value → metric types the segment's minutes count towards
SLEEP_VALUES: dict[str, tuple[str, ...]] = {
    "HKCategoryValueSleepAnalysisInBed": ("sleep_inbed_minutes",),
    "HKCategoryValueSleepAnalysisAsleepCore": ("sleep_core_minutes", "sleep_asleep_minutes"),
    "HKCategoryValueSleepAnalysisAsleepDeep": ("sleep_deep_minutes", "sleep_asleep_minutes"),
    "HKCategoryValueSleepAnalysisAsleepREM": ("sleep_rem_minutes", "sleep_asleep_minutes"),
    "HKCategoryValueSleepAnalysisAsleepUnspecified": ("sleep_asleep_minutes",),
    "HKCategoryValueSleepAnalysisAsleep": ("sleep_asleep_minutes",),
}
SLEEP_AWAKE = "HKCategoryValueSleepAnalysisAwake"
_TOP_LEVEL_TAGS = {"Record", "Correlation", "Workout", "ActivitySummary", "ClinicalRecord", "Audiogram",
                   "VisionPrescription", "Me", "ExportDate"}


@dataclass
class RhythmEvent:
    source: str        # RHYTHM_SOURCES
    raw_label: str
    label: str         # normalised RHYTHM_LABELS
    recorded_at: datetime
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedExport:
    samples: list[MetricIn] = field(default_factory=list)
    rhythm_results: list[RhythmEvent] = field(default_factory=list)
    bp_readings: list[BpIn] = field(default_factory=list)
    ecg_files: list[tuple[str, bytes]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def parse_hk_date(s: str) -> datetime:
    return datetime.strptime(s.strip(), HK_DATE_FMT)


def _day_start(d: date, ref: datetime) -> datetime:
    """Local midnight of `d` in the UTC offset of `ref`."""
    return datetime.combine(d, time.min, tzinfo=ref.tzinfo)


# ---------------------------------------------------------------- input handling

def _open_inputs(src: bytes | str | Path | IO[bytes]) -> tuple[Callable[[], IO[bytes]], zipfile.ZipFile | None]:
    """Return (opener for export.xml, ZipFile or None)."""
    if isinstance(src, (str, Path)):
        path = Path(src)
        if zipfile.is_zipfile(path):
            zf = zipfile.ZipFile(path)
        else:
            return (lambda: path.open("rb")), None
    elif isinstance(src, (bytes, bytearray)):
        if bytes(src[:2]) == b"PK":
            zf = zipfile.ZipFile(io.BytesIO(src))
        else:
            data = bytes(src)
            return (lambda: io.BytesIO(data)), None
    else:  # file object
        if zipfile.is_zipfile(src):
            src.seek(0)
            zf = zipfile.ZipFile(src)
        else:
            src.seek(0)
            return (lambda: src), None
    names = [n for n in zf.namelist() if n.rsplit("/", 1)[-1] == "export.xml"]
    if not names:
        raise ValueError("zip does not contain export.xml")
    member = min(names, key=len)
    return (lambda: zf.open(member)), zf


# ---------------------------------------------------------------- parser

def parse_apple_health_export(src: bytes | str | Path | IO[bytes], user_id: str | None = None,
                              since: date | None = None) -> ParsedExport:
    """Parse an Apple Health `export.zip` (or bare `export.xml`) without loading it whole.

    `since`: ignore records whose local start date is before this date. `user_id` is unused
    by the pure parser (kept for logging/symmetry with the import function)."""
    opener, zf = _open_inputs(src)
    out = ParsedExport()
    counts: dict[str, int] = defaultdict(int)

    # (metric, local day) → source → values ; remember a reference datetime for the offset
    qty: dict[tuple[str, date], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    qty_ref: dict[tuple[str, date], datetime] = {}
    sleep: dict[tuple[str, date], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    sleep_ref: dict[date, datetime] = {}
    bp: dict[datetime, dict[str, float]] = defaultdict(dict)
    seen_bp: set[tuple[str, datetime]] = set()

    with opener() as fh:
        depth = 0
        root = None
        for event, elem in iterparse(fh, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = elem
                depth += 1
                continue
            depth -= 1
            tag = elem.tag
            if tag == "Record":
                counts["records_seen"] += 1
                try:
                    _handle_record(elem.attrib, since, counts, qty, qty_ref, sleep, sleep_ref, bp, seen_bp, out)
                except (ValueError, KeyError):
                    counts["records_unparseable"] += 1
            if depth == 1 and tag in _TOP_LEVEL_TAGS:
                elem.clear()
                if root is not None:
                    root.clear()  # drop references to consumed siblings

    # quantity → daily samples
    for (mtype, d), by_src in sorted(qty.items()):
        agg = METRIC_TYPES[mtype].agg
        if agg == "sum":
            value = max(sum(v) for v in by_src.values())
        else:
            value = fmean(x for v in by_src.values() for x in v)
        n = sum(len(v) for v in by_src.values())
        out.samples.append(MetricIn(type=mtype, value=round(value, 4), recorded_at=_day_start(d, qty_ref[(mtype, d)]),
                                    source=SOURCE, meta={"n": n, "agg": agg, "local_date": d.isoformat()}))
    # sleep → nightly samples (per-source totals, max across sources)
    nightly: dict[tuple[str, date], float] = {}
    for (mtype, night), by_src in sleep.items():
        nightly[(mtype, night)] = max(by_src.values())
    for (mtype, night), value in sorted(nightly.items()):
        mt = METRIC_TYPES[mtype]
        value = min(round(value, 1), mt.max)
        out.samples.append(MetricIn(type=mtype, value=value, recorded_at=_day_start(night, sleep_ref[night]),
                                    source=SOURCE, meta={"night_of": night.isoformat(), "agg": "sum"}))
    counts["nights"] = len({n for _, n in nightly})
    # BP pairs
    for ts, pair in sorted(bp.items()):
        if "sys" in pair and "dia" in pair:
            out.bp_readings.append(BpIn(systolic=round(pair["sys"]), diastolic=round(pair["dia"]), measured_at=ts,
                                        session=session_for_hour(ts.hour), source=SOURCE))
        else:
            counts["bp_unpaired"] += 1
    # ECG CSVs
    if zf is not None:
        for name in zf.namelist():
            parts = name.split("/")
            if len(parts) >= 2 and parts[-2] == "electrocardiograms" and name.lower().endswith(".csv"):
                data = zf.read(name)
                ev = parse_ecg_csv(data, parts[-1])
                if ev is None:
                    counts["ecg_unparseable"] += 1
                    continue
                if since and ev.recorded_at.date() < since:
                    continue
                out.ecg_files.append((parts[-1], data))
                out.rhythm_results.append(ev)
                counts["ecg_files"] += 1
        zf.close()

    counts["samples"] = len(out.samples)
    counts["bp_readings"] = len(out.bp_readings)
    counts["rhythm_results"] = len(out.rhythm_results)
    out.counts = dict(counts)
    return out


def _handle_record(a: dict[str, str], since: date | None, counts, qty, qty_ref, sleep, sleep_ref, bp, seen_bp,
                   out: ParsedExport) -> None:
    rtype = a.get("type", "")
    if rtype in QUANTITY_MAP:
        start = parse_hk_date(a["startDate"])
        d = start.date()
        if since and d < since:
            return
        mtype, conv = QUANTITY_MAP[rtype]
        fn = conv.get(a.get("unit")) or conv.get(None) or _ID[None]
        qty[(mtype, d)][a.get("sourceName", "?")].append(fn(float(a["value"])))
        qty_ref.setdefault((mtype, d), start)
        counts[f"hk:{mtype}"] += 1
    elif rtype in (BP_SYS, BP_DIA):
        start = parse_hk_date(a["startDate"])
        if since and start.date() < since:
            return
        key = (rtype, start)
        if key in seen_bp:  # same record nested in a Correlation and repeated
            return
        seen_bp.add(key)
        bp[start]["sys" if rtype == BP_SYS else "dia"] = float(a["value"])
    elif rtype == SLEEP:
        start, end = parse_hk_date(a["startDate"]), parse_hk_date(a["endDate"])
        night = (end + timedelta(hours=6)).date()
        if since and night < since:
            return
        value = a.get("value", "")
        src = a.get("sourceName", "?")
        sleep_ref.setdefault(night, end)
        if value == SLEEP_AWAKE:
            sleep[("sleep_awake_count", night)][src] += 1
        else:
            minutes = max(0.0, (end - start).total_seconds() / 60)
            for mtype in SLEEP_VALUES.get(value, ()):
                sleep[(mtype, night)][src] += minutes
        counts["hk:sleep_segments"] += 1
    elif rtype in RHYTHM_EVENTS:
        start = parse_hk_date(a["startDate"])
        if since and start.date() < since:
            return
        out.rhythm_results.append(RhythmEvent(source="apple_irn", raw_label=rtype, label=RHYTHM_EVENTS[rtype],
                                              recorded_at=start, meta={"device": a.get("sourceName")}))
        counts[f"hk:{RHYTHM_EVENTS[rtype]}"] += 1


# ---------------------------------------------------------------- ECG CSV

_ECG_KEYS = {
    "recorded date": "recorded_at", "記録日": "recorded_at", "記録日時": "recorded_at",
    "classification": "classification", "分類": "classification",
    "sample rate": "sample_rate", "サンプルレート": "sample_rate",
    "device": "device", "デバイス": "device", "software version": "software_version", "lead": "lead",
    "unit": "unit", "symptoms": "symptoms", "症状": "symptoms",
}


def parse_ecg_csv(data: bytes, filename: str = "ecg.csv") -> RhythmEvent | None:
    """Read the header block of an Apple `electrocardiograms/*.csv` (key,value lines until the
    first blank line). Name / date of birth lines are intentionally dropped."""
    text = data.decode("utf-8-sig", errors="replace")
    header: dict[str, str] = {}
    for row in csv.reader(io.StringIO(text)):
        if not row or not any(c.strip() for c in row):
            if header:
                break
            continue
        key = _ECG_KEYS.get(row[0].strip().lower()) or _ECG_KEYS.get(row[0].strip())
        if key:
            header[key] = ",".join(c for c in row[1:]).strip()
        elif len(header) > 0 and _is_number(row[0]):
            break
    if "classification" not in header or "recorded_at" not in header:
        return None
    try:
        recorded = parse_hk_date(header["recorded_at"])
    except ValueError:
        try:
            recorded = datetime.fromisoformat(header["recorded_at"])
        except ValueError:
            return None
    meta = {k: v for k, v in header.items() if k not in ("recorded_at", "classification")}
    meta["file_name"] = filename
    raw = header["classification"]
    return RhythmEvent(source="apple_ecg", raw_label=raw, label=normalize_rhythm_label("apple_ecg", raw),
                       recorded_at=recorded, meta=meta)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- persist

def import_apple_health(db: Session, user_id: str, src: bytes | str | Path, storage=None,
                        since: date | None = None) -> dict[str, Any]:
    """Parse + store: metrics via upsert_metrics, BP via add_bp_readings, rhythm results via
    add_rhythm_result (ECG CSVs saved through storage and linked by file_url). Flushes only."""
    parsed = parse_apple_health_export(src, user_id, since)
    up = upsert_metrics(db, user_id, parsed.samples, default_source=SOURCE)
    bp_rows = add_bp_readings(db, user_id, parsed.bp_readings) if parsed.bp_readings else []
    files = {name: data for name, data in parsed.ecg_files}
    n_rhythm = 0
    for ev in parsed.rhythm_results:
        file_url = None
        fname = ev.meta.get("file_name")
        if fname and storage is not None and fname in files:
            file_url = storage.save(user_id, "ecg", fname, files[fname])
        add_rhythm_result(db, user_id, ev.source, ev.raw_label, ev.recorded_at, file_url=file_url, meta=ev.meta)
        n_rhythm += 1
    return {"parse": parsed.counts, "metrics": up.as_dict(), "bp_readings": len(bp_rows), "rhythm_results": n_rhythm}
