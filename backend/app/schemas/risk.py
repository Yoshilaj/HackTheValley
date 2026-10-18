"""Risk assessment request/response models (spec phase-a §10, §11)."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class RiskAssessmentOut(ORMModel):
    """Full stored assessment (§10 output contract)."""

    id: str
    pathway: str
    level: str
    priority_score: int | None = None
    fired_rules: list[dict[str, Any]] | None = None
    missing_inputs: list[str] | None = None
    data_quality: dict[str, Any] | None = None
    model_versions: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] | None = None
    probabilities: list[dict[str, Any]] | None = None
    preventive_measures: list[dict[str, Any]] | None = None
    explanation: str | None = None
    created_at: datetime


class RiskHistoryPoint(BaseModel):
    week: str
    pathway: str
    level: str
    priority_score: int | None = None
    assessment_id: str


class RiskRunOut(BaseModel):
    user_id: str
    trigger: str
    assessments: list[RiskAssessmentOut]


class RiskStatusOut(BaseModel):
    """When the user was last assessed and when the scheduled re-assessments run next (API.md §9)."""

    last_assessed_at: datetime | None
    last_trigger: str | None  # manual | nightly | weekly | event
    scheduler_running: bool
    next_runs: dict[str, str | None]  # job name -> ISO time, None when the scheduler is stopped

