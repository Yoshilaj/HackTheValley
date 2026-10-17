"""Health-data ingestion schemas (BACKEND_PLAN C1 / A5 §1, §3, §4).

`METRIC_TYPES` is the registry of accepted `MetricSample.type` values: unit,
plausible range (values outside are rejected, not clamped) and the daily
aggregation rule used by `metrics_summary` and the Apple Health parser
(`sum` for cumulative types, `mean` for level types).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


@dataclass(frozen=True)
class MetricType:
    unit: str
    min: float
    max: float
    agg: Literal["sum", "mean"]
    description: str = ""


METRIC_TYPES: dict[str, MetricType] = {
    # Activity (A5 §1) — cumulative per day
    "steps": MetricType("count", 0, 100_000, "sum", "Step count"),
    "distance_m": MetricType("m", 0, 200_000, "sum", "Walking + running distance"),
    "active_energy_kcal": MetricType("kcal", 0, 10_000, "sum", "Active energy burned"),
    "exercise_minutes": MetricType("min", 0, 1440, "sum", "Apple exercise minutes"),
    # Heart
    "resting_hr": MetricType("bpm", 20, 200, "mean", "Resting heart rate"),
    "walking_hr_avg": MetricType("bpm", 30, 220, "mean", "Walking heart-rate average"),
    "hrv_sdnn_ms": MetricType("ms", 1, 500, "mean", "Heart-rate variability (SDNN)"),
    "vo2max": MetricType("mL/kg/min", 5, 100, "mean", "Cardio fitness (VO2max)"),
    # Respiratory / oxygen
    "spo2_pct": MetricType("%", 50, 100, "mean", "Blood oxygen saturation (percent, not fraction)"),
    "respiratory_rate": MetricType("breaths/min", 4, 60, "mean", "Respiratory rate"),
    # Sleep (one value per night, recorded_at = wake date 00:00 local)
    "sleep_asleep_minutes": MetricType("min", 0, 1440, "sum", "Total asleep (all stages)"),
    "sleep_inbed_minutes": MetricType("min", 0, 1440, "sum", "Time in bed"),
    "sleep_core_minutes": MetricType("min", 0, 1440, "sum", "Core sleep"),
    "sleep_deep_minutes": MetricType("min", 0, 1440, "sum", "Deep sleep"),
    "sleep_rem_minutes": MetricType("min", 0, 1440, "sum", "REM sleep"),
    "sleep_awake_count": MetricType("count", 0, 200, "sum", "Awake segments during the night"),
    # Mobility (spec §5.2, PD-02)
    "walking_speed_mps": MetricType("m/s", 0.05, 3.0, "mean", "Walking speed"),
    "step_length_m": MetricType("m", 0.05, 1.5, "mean", "Walking step length"),
    "double_support_pct": MetricType("%", 0, 100, "mean", "Double-support time (percent)"),
    "walking_asymmetry_pct": MetricType("%", 0, 100, "mean", "Walking asymmetry (percent)"),
    "walking_steadiness": MetricType("%", 0, 100, "mean", "Apple walking steadiness (percent)"),
    # Body
    "weight_kg": MetricType("kg", 20, 400, "mean", "Body mass"),
    "bmi": MetricType("kg/m2", 10, 80, "mean", "Body-mass index"),
    "body_temp_c": MetricType("degC", 30, 45, "mean", "Body temperature"),
}

#: Series names used by app/ml/rules.py (spec §5.2, §6) → our metric types.
#: The context builder should read these rows under the rule-engine name.
RULE_SERIES_ALIASES: dict[str, str] = {
    "walking_speed": "walking_speed_mps",
    "step_length": "step_length_m",
    "double_support_pct": "double_support_pct",
    "walking_asymmetry_pct": "walking_asymmetry_pct",
    "sleep_duration": "sleep_asleep_minutes",
}


# ---------------------------------------------------------------- metrics

class MetricIn(BaseModel):
    """One sample. Naive `recorded_at` is interpreted in the user's profile timezone."""

    type: str = Field(description="Key of METRIC_TYPES", examples=["steps"])
    value: float = Field(examples=[8421])
    unit: str | None = Field(default=None, description="Optional; defaults to the registry unit")
    recorded_at: datetime = Field(examples=["2026-10-17T00:00:00+09:00"])
    source: str | None = Field(default=None, description="METRIC_SOURCES; defaults to the batch source")
    meta: dict[str, Any] | None = None


class MetricBatchIn(BaseModel):
    source: str = Field(default="manual", description="Default source for samples without one",
                        examples=["shortcut"])
    samples: list[MetricIn]

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "source": "shortcut",
        "samples": [
            {"type": "steps", "value": 8421, "recorded_at": "2026-10-17T00:00:00+09:00"},
            {"type": "resting_hr", "value": 61, "recorded_at": "2026-10-17T07:10:00+09:00"},
        ],
    }]})


class RejectedItem(BaseModel):
    index: int
    reason: str


class UpsertResultOut(BaseModel):
    inserted: int
    updated: int = 0
    duplicates: int
    rejected: list[RejectedItem] = []


class MetricOut(ORMModel):
    id: str
    type: str
    value: float
    unit: str | None
    recorded_at: datetime
    source: str


# ---------------------------------------------------------------- blood pressure

class BpIn(BaseModel):
    """One cuff reading (spec §2). `session` is derived from the local hour when omitted
    (04–11 morning, 17–23 evening, else other); `reading_no` is auto-assigned.

    Bounds here reject only impossible values (≤0 or >300 mmHg / bpm) with a 422. Implausible but
    typable readings (e.g. 120/20, 300/90, pulse 20) are deliberately *accepted and stored as
    invalid* (JSH 2019: every reading is recorded) by `ingest_service.bp_validity`, which applies
    the spec §2 ranges (systolic 70–260 > diastolic 40–150, pulse 30–200)."""

    systolic: int = Field(gt=0, le=300, examples=[142])
    diastolic: int = Field(gt=0, le=300, examples=[91])
    pulse: int | None = Field(default=None, gt=0, le=300, examples=[72])
    measured_at: datetime = Field(examples=["2026-10-17T07:05:00+09:00"])
    session: Literal["morning", "evening", "other"] | None = None
    reading_no: int | None = Field(default=None, ge=1, le=10)
    irregular_pulse_flag: bool = False
    arm: Literal["left", "right"] | None = None
    position: Literal["seated", "standing", "supine"] | None = None
    pre_medication: bool | None = None
    user_marked_error: bool = Field(default=False, description="User says this reading was an error")
    source: str = "manual"


class BpBatchIn(BaseModel):
    readings: list[BpIn]

    model_config = ConfigDict(json_schema_extra={"examples": [{"readings": [
        {"systolic": 142, "diastolic": 91, "pulse": 72, "measured_at": "2026-10-17T07:05:00+09:00"},
        {"systolic": 138, "diastolic": 89, "pulse": 70, "measured_at": "2026-10-17T07:07:00+09:00"},
    ]}]})


class BpOut(ORMModel):
    id: str
    session: str
    reading_no: int
    systolic: int
    diastolic: int
    pulse: int | None
    irregular_pulse_flag: bool
    measured_at: datetime
    arm: str | None
    position: str | None
    pre_medication: bool | None
    valid: bool
    invalid_reason: str | None
    source: str


# ---------------------------------------------------------------- rhythm

class RhythmIn(BaseModel):
    """Manual entry of a device rhythm result (spec §4). `label` may be the device's
    own string (e.g. 'Atrial Fibrillation', '心房細動の可能性'); it is normalised."""

    source: Literal["apple_irn", "apple_ecg", "omron_connect", "manual_irregular_pulse"]
    label: str = Field(examples=["心房細動の可能性"])
    recorded_at: datetime
    file_url: str | None = None
    meta: dict[str, Any] | None = None


class RhythmOut(ORMModel):
    id: str
    source: str
    label: str
    raw_label: str | None
    recorded_at: datetime
    file_url: str | None
    meta: dict[str, Any] | None = None


# ---------------------------------------------------------------- daily log

class DailyLogIn(BaseModel):
    """Daily check-in (A5 §3). Flags set to true become Observations (spec §7 red flags,
    §1.3 COG-03 functional changes)."""

    date: date
    sleep_quality: int | None = Field(default=None, ge=1, le=5)
    fatigue: int | None = Field(default=None, ge=1, le=5)
    mood: int | None = Field(default=None, ge=1, le=5)
    stress: int | None = Field(default=None, ge=1, le=5)
    flags: dict[str, bool] | None = None
    medication_adherence: bool | None = None
    caffeine_alcohol: dict[str, Any] | None = None
    exercise_minutes: int | None = Field(default=None, ge=0, le=1440)
    notes: str | None = Field(default=None, max_length=5000)

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "date": "2026-10-17", "sleep_quality": 2, "fatigue": 4, "mood": 3, "stress": 3,
        "flags": {"headache": True, "memory_lapse": True, "chest_discomfort": False},
        "medication_adherence": True, "caffeine_alcohol": {"caffeine_cups": 2, "alcohol_units": 0},
        "exercise_minutes": 20, "notes": "Forgot where I parked.",
    }]})


class ObservationOut(ORMModel):
    id: str
    kind: str
    label: str
    severity: str | None
    onset: str | None
    free_text: str | None
    source: str
    source_id: str | None
    created_at: datetime


class ObservationCreated(ObservationOut):
    """POST /observations response: the row + emergency guidance when it is (or its text
    contains) a red flag (spec §7)."""

    emergency_guidance: str | None = None


class ObservationCreate(BaseModel):
    kind: Literal["symptom", "functional_change", "medication", "lifestyle", "mood", "red_flag"]
    label: str = Field(min_length=1, max_length=200, examples=["dizziness"])
    severity: str | None = Field(default=None, max_length=32)
    onset: str | None = Field(default=None, max_length=100)
    free_text: str | None = Field(default=None, max_length=5000)
    source: Literal["chat", "log", "document", "screener"] = "log"


class DailyLogOut(ORMModel):
    id: str
    date: date
    sleep_quality: int | None
    fatigue: int | None
    mood: int | None
    stress: int | None
    flags: dict[str, bool] | None
    medication_adherence: bool | None
    caffeine_alcohol: dict[str, Any] | None
    exercise_minutes: int | None
    notes: str | None
    observations: list[ObservationOut] = []
    #: emergency guidance (spec §7) when this submission reported a red flag; else null
    emergency_guidance: str | None = None
