"""Upload / job schemas (BACKEND_PLAN C1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.common import ORMModel
from app.schemas.metrics import BpOut


class UploadOut(ORMModel):
    id: str
    kind: str
    status: str  # pending | processing | done | failed | pending_transcription
    extracted: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, json_schema_extra={"examples": [{
        "id": "0b6c…", "kind": "doctor_note", "status": "done", "created_at": "2026-10-17T03:00:00Z",
        "extracted": {"filename": "note.pdf", "data": {"diagnoses": ["Hypertension"],
                      "medications": [{"name": "Amlodipine", "dose": "5 mg", "schedule": "once daily"}],
                      "follow_ups": ["Recheck BP in 4 weeks"], "lab_values": [], "summary": "…"}},
    }]})


class JobOut(ORMModel):
    id: str
    name: str
    status: str  # pending | running | success | failed
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    result: dict[str, Any] | None = None
    created_at: datetime


class AppleHealthAccepted(BaseModel):
    job_id: str
    upload_id: str
    status: str = "pending"


class BpPhotoOut(BaseModel):
    document: UploadOut
    bp_readings: list[BpOut] = []
