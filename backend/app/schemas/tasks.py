"""Assessment-task API schemas (spec docs/specs/phase-a-assessments.md §1, §1.4, §5.3, §5.4, §10)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StoryStatusOut(BaseModel):
    """State of a story-recall battery (§1.1). ``audio_url`` is set for listen steps only."""

    task_id: str
    session_id: str
    language: str
    protocol_version: str
    steps: list[str]
    step_index: int
    current_step: str | None
    finished: bool
    instruction: str
    instructions: dict[str, str]  # {"en": ..., "ja": ...} for the current step
    audio_url: str | None = None
    delay_remaining_s: float | None = None
    max_recall_s: float
    result: "StoryResultOut | None" = None


class StoryResultOut(BaseModel):
    """Finalised session (§1.3). ``score`` = experimental semantic-recall mean (0-100), not G-match."""

    score: float | None
    score_name: str
    valid: bool
    status: str  # ok | invalid_measurement
    invalid_reasons: list[str]
    per_recall: dict[str, float | None]
    content_unit_fraction_mean: float | None
    story_ids: list[str]
    message: str


class VowelOut(BaseModel):
    """One sustained-vowel repetition (§5.4). The model score is detail-only (§11.2)."""

    task_id: str
    repetition_index: int
    valid: bool
    status: str  # ok | invalid_measurement
    recording_quality: dict[str, Any]
    reasons: list[str]
    message: str
    tips: list[str]
    experimental_score: dict[str, Any] | None = None


class TappingIn(BaseModel):
    """Pointer-down timestamps (ms) per hand for a 15 s alternate-tap trial (§5.3)."""

    left_ms: list[float] = Field(default_factory=list, max_length=2000)
    right_ms: list[float] = Field(default_factory=list, max_length=2000)
    duration_s: float = Field(default=15.0, gt=0, le=60)


class TappingOut(BaseModel):
    task_id: str
    valid: bool
    reason: str | None
    features: dict[str, Any]
    message: str


class WordRecallStartIn(BaseModel):
    language: str | None = None


class WordRecallStartOut(BaseModel):
    task_id: str
    set_id: str
    language: str
    words: list[str]
    instruction: str
    recall_prompt: str
    presented_at: str


class WordRecallSubmitIn(BaseModel):
    response_text: str = Field(default="", max_length=2000)
    latency_ms: float | None = Field(default=None, ge=0)


class WordRecallOut(BaseModel):
    task_id: str
    score: int
    max_score: int
    recalled: list[str]
    missed: list[str]
    latency_ms: float | None
    valid: bool


class TaskHistoryItem(BaseModel):
    id: str
    task_type: str
    created_at: datetime
    status: str  # in_progress | complete
    score: float | None
    valid: bool
    invalid_reason: str | None
    language: str | None
    protocol_version: str | None
    model_scores: dict[str, Any] = Field(default_factory=dict)


class TaskHistoryOut(BaseModel):
    items: list[TaskHistoryItem]


StoryStatusOut.model_rebuild()
