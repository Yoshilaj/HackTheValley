"""Risk assessment + screener request/response models (spec phase-a §5.1, §10, §11)."""
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


class ScreenerItemOut(BaseModel):
    id: str
    type: str
    text: str


class ScreenerOut(BaseModel):
    id: str
    version: str
    language: str
    period: str
    response_type: str = "yes_no"
    items: list[ScreenerItemOut]


class ScreenerAnswersIn(BaseModel):
    answers: dict[str, bool] = Field(..., description="item id (content-bank id; legacy pd_* ids accepted) → yes/no")


class ScreenerResultOut(BaseModel):
    id: str
    screener: str
    score: float
    motor_count: int
    nonmotor_count: int
    pd01_would_fire: bool
    next_tasks: list[str] = Field(default_factory=list)
    created_at: datetime
