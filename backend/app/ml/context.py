"""Plain input dataclasses for the Phase A assessment engine.

The DB layer maps ORM rows to these (``docs/specs/phase-a-assessments.md``
§8 "Rule engine contract"). Nothing in ``app.ml`` imports from ``app.db`` or
``app.core``; the engine is pure and deterministic given a ``PathwayContext``.

Conventions
-----------
* Datetimes may be naive or tz-aware, but all values in one context must use
  the same convention. Calendar-day grouping uses ``datetime.date()``, so the
  mapper should convert to the user's local timezone first.
* Every input row has an optional ``id`` (last field) that is copied into
  ``evidence[].ref`` so the report/API can link back to the row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

#: Pathways evaluated by the engine. ``general`` carries GEN-01 and EMG-01.
PATHWAYS: tuple[str, ...] = ("cognitive", "cardio", "parkinsons", "general")
#: Country whose guidelines select region-specific rules/calculators (ISO 3166-1 alpha-2).
#: "CA" (default): Framingham Risk Score / CCS; "JP": Suita score (kept, not used in Canada).
DEFAULT_REGION = "CA"


@dataclass(frozen=True)
class BpReadingIn:
    """One cuff reading (spec §2 data model). ``session`` is morning|evening.

    ``valid=False`` means the user marked the reading as an error; the other
    validity rules (spec §2 'Validity') are applied by ``app.ml.bp``.
    """

    measured_at: datetime
    session: str
    systolic: int
    diastolic: int
    pulse: int | None = None
    irregular_pulse_flag: bool = False
    valid: bool = True
    id: str | None = None


@dataclass(frozen=True)
class RhythmResultIn:
    """Device rhythm result (spec §4). ``label`` may be the raw device string
    or an already-normalised enum value; ``app.ml.rhythm`` normalises it."""

    recorded_at: datetime
    source: str
    label: str
    id: str | None = None


@dataclass(frozen=True)
class ObservationIn:
    """Extracted observation (symptom, functional change, red flag, ...)."""

    created_at: datetime
    kind: str
    label: str
    severity: int | None = None
    free_text: str | None = None
    id: str | None = None


@dataclass(frozen=True)
class DailySeries:
    """A numeric series of (date, value). Several values per date are allowed
    and are reduced to the daily median by ``app.ml.baseline``.

    Series names used by the rules: ``walking_speed``, ``step_length``,
    ``double_support_pct``, ``walking_asymmetry_pct`` (PD-02);
    ``tap_count``, ``tap_interval_mean``, ``tap_interval_cv`` (PD-04);
    ``sleep_duration``, ``fatigue`` (GEN-01).
    """

    name: str
    points: list[tuple[date, float]] = field(default_factory=list)


@dataclass(frozen=True)
class TaskSessionIn:
    """One assessment-task session (``AssessmentTask`` row).

    ``task_type``: story_recall | sustained_vowel | word_recall | finger_tap ...
    ``score``: the task's primary score (story_recall → semantic_recall_mean).
    ``valid``: recording quality gate passed.
    ``model_scores``: e.g. ``{"pd_vowel": {"probability": 0.7, "threshold": 0.6,
    "version": "v1"}}``.
    ``features``: the ``AssessmentTask.features`` JSON (sustained_vowel: UCI keys +
    ``CPPS``; story_recall: ``temporal[label]`` speech-rate / pause-ratio dicts) — read by
    PD-03B (spec §5.5).
    ``device``: coarse recording-device label (same device required for a voice baseline);
    ``None`` = unknown.
    ``recording_quality``: the ``AssessmentTask.recording_quality`` JSON (``noise_floor_dbfs`` …).
    """

    created_at: datetime
    task_type: str
    score: float | None = None
    valid: bool = True
    model_scores: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    features: dict[str, Any] = field(default_factory=dict)
    device: str | None = None
    recording_quality: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScreenerIn:
    """Screener response; ``answers`` keyed by item id (``app.ml.screener``)."""

    created_at: datetime
    screener: str
    answers: dict[str, bool] = field(default_factory=dict)
    score: float | None = None
    id: str | None = None


@dataclass(frozen=True)
class ProfileIn:
    """Static profile. ``None`` always means *unknown* — never assumed normal."""

    age: int | None = None
    sex: str | None = None  # "male" | "female"
    smoker: bool | None = None
    diabetes: bool | None = None
    ckd_stage: int | None = None
    egfr: float | None = None
    ldl: float | None = None
    hdl: float | None = None
    total_chol: float | None = None
    #: on blood-pressure-lowering medication (Framingham "SBP if treated"); None = unknown
    bp_treated: bool | None = None
    #: first-degree relative with premature CVD (men ≤55, women ≤65; CCS 2021); None = unknown
    family_history_premature_cvd: bool | None = None
    known_af: bool = False
    known_chd: bool = False
    known_pd: bool = False
    known_dementia: bool = False


@dataclass(frozen=True)
class PriorAssessment:
    """A previous ``RiskAssessment`` for the same pathway.

    ``fired_rules`` is a list of rule ids (dicts with an ``id`` key are also
    accepted by the engine).
    """

    created_at: datetime
    level: str
    fired_rules: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class PathwayContext:
    """Everything the engine needs for one user × pathway at ``as_of``."""

    user_id: str
    pathway: str
    as_of: datetime
    profile: ProfileIn = field(default_factory=ProfileIn)
    region: str = DEFAULT_REGION
    bp_readings: list[BpReadingIn] = field(default_factory=list)
    rhythm_results: list[RhythmResultIn] = field(default_factory=list)
    observations: list[ObservationIn] = field(default_factory=list)
    series: dict[str, DailySeries] = field(default_factory=dict)
    task_sessions: list[TaskSessionIn] = field(default_factory=list)
    screeners: list[ScreenerIn] = field(default_factory=list)
    prior_assessments: list[PriorAssessment] = field(default_factory=list)
