"""Dashboard / pathway-card response models (spec phase-a §11.4)."""
from typing import Any, Literal

from pydantic import BaseModel, Field


class Driver(BaseModel):
    rule_id: str
    text: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class TrendPoint(BaseModel):
    week: str
    priority_score: int | None = None


class PreventiveMeasure(BaseModel):
    text: str | None = None
    source_id: str | None = None


class NextAction(BaseModel):
    # Matches dashboard_service.NEXT_ACTION_BY_STATUS and docs/API.md (the approval and needs-info
    # values were missing, so /api/risk/latest failed with a 500 once an appointment awaited approval).
    type: Literal["appointment_approval_needed", "appointment_requested", "appointment_needs_info",
                  "appointment_confirmed", "none", "collect_data"]
    appointment_id: str | None = None


class WhatIf(BaseModel):
    change: str | None = None
    percent_10y: float | None = None
    display: str | None = None
    category: str | None = None


class LongTermRisk(BaseModel):
    """Long-term cardio context estimate (never drives the level). Canada: Framingham Risk Score
    (D'Agostino 2008, CCS 2021 usage); ``suita`` only for region JP."""

    calculator: Literal["framingham_2008", "suita"]
    name: str | None = None
    status: str | None = None          # ok | insufficient_data | not_applicable
    reason: str | None = None
    percent_10y: float | None = None
    display: str | None = None
    category: str | None = None        # CCS: low | intermediate | high (Suita: elevated | not_elevated)
    inputs: dict[str, Any] = Field(default_factory=dict)
    inputs_used: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    what_if: list[WhatIf] = Field(default_factory=list)
    source_id: str | None = None
    guideline_source_id: str | None = None
    note: str | None = None


class PathwayCard(BaseModel):
    pathway: str
    level: str
    priority_score: int | None = None
    score_kind: str
    score_label: str
    headline: str
    drivers: list[Driver] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    trend: list[TrendPoint] = Field(default_factory=list)
    probabilities: list[dict[str, Any]] = Field(default_factory=list)
    long_term_risk: LongTermRisk | None = None
    preventive_measures: list[PreventiveMeasure] = Field(default_factory=list)
    next_action: NextAction
    disclaimer: str


class NotificationsBlock(BaseModel):
    unread: int
    recent: list[dict[str, Any]] = Field(default_factory=list)


class DashboardOut(BaseModel):
    pathways: list[PathwayCard]
    metrics_summary: dict[str, Any] = Field(default_factory=dict)
    appointments: list[dict[str, Any]] = Field(default_factory=list)
    notifications: NotificationsBlock
