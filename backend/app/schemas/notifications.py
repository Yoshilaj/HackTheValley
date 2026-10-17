"""Schemas for notifications and admin job endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.common import ORMModel


class NotificationOut(ORMModel):
    id: str
    kind: str
    title: str
    body: str | None = None
    payload: dict[str, Any] | None = None
    read_at: datetime | None = None
    created_at: datetime


class UnreadCountOut(BaseModel):
    unread: int


class ReadAllOut(BaseModel):
    updated: int


class JobOut(ORMModel):
    id: str
    name: str
    status: str
    scheduled_for: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime


class SchedulerJobOut(BaseModel):
    name: str
    schedule: str
    next_run: str | None = None


class SchedulerStatusOut(BaseModel):
    running: bool
    timezone: str
    jobs: list[SchedulerJobOut]
