"""Shared helpers for the booking-agent tests (fakes only, never the network)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import CONSENT_PURPOSES, Notification
from app.services.booking.channels.base import ChannelMessage, ChannelResult
from tests.test_report import make_assessment, make_user  # noqa: F401

TORONTO = (43.6629, -79.3957)  # near Bloor St W
RAW_HEALTH = ("146/92", "92 mmHg", "AFib", "possible_af", "palpitations", "Amlodipine", "BP-02", "RHY-01")


def toronto_user(db, consents=CONSENT_PURPOSES, **profile: Any):
    user = make_user(db, consents=consents, lat=TORONTO[0], lng=TORONTO[1])
    p = user.profile
    p.timezone, p.postal_code, p.province = "America/Toronto", "M5S 1X5", "ON"
    p.phone, p.display_name = "+1 416-555-0199", "Alex Tremblay"
    p.address = "401 Bloor St W, Toronto"
    for k, v in profile.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(user)
    return user


def notes(db, user, kind=None) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if kind:
        stmt = stmt.where(Notification.kind == kind)
    return list(db.scalars(stmt.order_by(Notification.created_at)))


def clinic_mail(outbox, kind="booking_request"):
    return [m for m in outbox if (m.tags or {}).get("kind") == kind]


class ScriptedChannel:
    """A channel whose availability and result are fixed; records execute calls."""

    def __init__(self, name: str, reason: str | None = None, result: ChannelResult | None = None,
                 configured: bool = True):
        self.name, self.reason, self.result, self._configured = name, reason, result, configured
        self.calls: list[str] = []

    def configured(self) -> bool:
        return self._configured

    def available(self, clinic, ctx):
        return self.reason

    def execute(self, appt, ctx):
        self.calls.append(ctx.clinic.id)
        r = self.result or ChannelResult.failed("scripted failure")
        if r.status in ("requested", "confirmed") and not r.messages:
            r.messages = [ChannelMessage(direction="out", channel=self.name, recipient="x",
                                         body=str(ctx.patient.shared_fields()))]
        return r


@pytest.fixture(autouse=True)
def no_web(monkeypatch):
    """Pin the web channel to 'not configured' (Playwright may be installed): these tests cover the
    planning / e-mail paths; the web channel has its own tests. Import this name to activate it."""
    from app.services.booking.channels.web import WebChannel

    monkeypatch.setattr(WebChannel, "configured", lambda self: False)
