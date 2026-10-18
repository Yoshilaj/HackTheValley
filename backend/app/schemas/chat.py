"""Chat, check-in, ingestion and research-source schemas (docs/API.md "Chat / RAG")."""

from __future__ import annotations

import datetime as _dt
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.metrics import ObservationOut


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    language: Literal["ja", "en"] | None = None
    # Continue one of your conversations (404 if it isn't yours), or start a new one titled from
    # this message. With neither, the turn joins your unthreaded messages (iOS Shortcut, older clients).
    conversation_id: str | None = Field(default=None, max_length=36)
    new_conversation: bool = False


class SourceRef(BaseModel):
    n: int
    id: str
    title: str
    year: int | None = None
    url: str | None = None
    snippet: str = ""
    chunk_id: str | None = None
    section: str | None = None  # parent section heading of the passage (e.g. "Results", "p. 6")
    score: float | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceRef] = []
    cited: list[int] = []  # SourceRef.n values that appear as [n] in the answer (subset of sources)
    observations_created: list[ObservationOut] = []
    assessment_hint: str | None = None
    message_id: str | None = None
    language: str | None = None
    conversation_id: str | None = None  # the conversation this turn was saved in


class VoiceChatResponse(ChatResponse):
    transcript: str
    task_id: str | None = None


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str | None = None
    role: str
    content: str
    audio_url: str | None = None
    sources: list[dict[str, Any]] | None = None
    created_at: datetime


class ConversationOut(BaseModel):
    """One of the user's chat conversations (GET /api/chat/conversations)."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class CheckinQuestion(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    text: str
    type: Literal["scale", "bool", "text"]
    reason: str = ""


class CheckinSubmit(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict, examples=[{"sleep_quality": 3, "palpitations": True}])
    date: _dt.date | None = None
    language: Literal["ja", "en"] | None = None


class CheckinResult(BaseModel):
    date: _dt.date
    daily_log_id: str
    observations: list[ObservationOut] = []
    ignored: list[str] = []


class IngestRequest(BaseModel):
    text: str = Field(min_length=1)
    source: str = Field(default="manual", min_length=1, max_length=100)
    title: str | None = None
    pathway_tags: list[str] | None = None
    url: str | None = None
    year: int | None = None


class IngestResponse(BaseModel):
    chunks_added: int
    source_id: str


class ResearchSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    authors: str | None = None
    year: int | None = None
    url: str | None = None
    doi: str | None = None
    pathway_tags: list[str] | None = None
    kind: str | None = None
    chunk_count: int = 0
