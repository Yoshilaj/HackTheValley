"""Schemas for clinician reports (C7)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ReportSummaryOut(BaseModel):
    id: str
    assessment_id: str | None = None
    pathway: str | None = None
    level: str | None = None
    share_url: str | None = None  # null until 'share_with_clinic' consent exists / after revoke
    html_url: str
    pdf_url: str | None = None
    created_at: datetime

    model_config = ConfigDict(json_schema_extra={"example": {
        "id": "4f0c…", "assessment_id": "9b1e…", "pathway": "cardio", "level": "followup_recommended",
        "share_url": "http://localhost:8000/r/Xk3…", "html_url": "/api/reports/4f0c…/html",
        "pdf_url": None, "created_at": "2026-10-17T09:00:00Z"}})


class ReportOut(ReportSummaryOut):
    markdown: str

    model_config = ConfigDict(json_schema_extra={"example": {
        "id": "4f0c…", "assessment_id": "9b1e…", "pathway": "cardio", "level": "followup_recommended",
        "share_url": "http://localhost:8000/r/Xk3…", "html_url": "/api/reports/4f0c…/html", "pdf_url": None,
        "created_at": "2026-10-17T09:00:00Z",
        "markdown": "# Referral summary from home monitoring\n\n## Summary for the clinician\n…"}})
