"""Shared emergency path for red flags reported by the user (spec phase-a §7).

    guidance = handle_red_flags(db, user, labels, source="log", source_id=row.id,
                                language="en", observations=red_flag_obs)

Used by chat, the daily check-in, ``POST /logs/daily`` and ``POST /observations``:

1. emergency guidance text (``guardrails.emergency_response``: 911, 811 nurse line, 9-8-8 — Canada);
2. ``notify.notify_emergency`` — skipped when every triggering observation id was already
   part of an emergency notification in the last 24 h (throttle, see
   :func:`already_notified`); the ids are stored in the notification payload;
3. ``risk_service.assess_user(trigger='event')`` so a ``RiskAssessment(level='emergency')``
   exists immediately. That run sees the ids as notified and does not notify again.

Never commits; the caller commits. Notification / assessment failures are logged and never
block the guidance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Notification, Observation, User

log = get_logger(__name__)

NOTIFY_THROTTLE = timedelta(hours=24)


def notified_ids(db: Session, user_id: str, within: timedelta = NOTIFY_THROTTLE,
                 now: datetime | None = None) -> set[str]:
    """Trigger ids (observation / evidence refs) recorded in emergency notifications within ``within``."""
    from app.services.notify import KIND_EMERGENCY

    now = now or datetime.now(timezone.utc)
    rows = db.scalars(select(Notification).where(
        Notification.user_id == user_id, Notification.kind == KIND_EMERGENCY,
        Notification.created_at >= now - within))
    out: set[str] = set()
    for r in rows:
        out.update(str(i) for i in ((r.payload or {}).get("observation_ids") or []))
    return out


def already_notified(db: Session, user_id: str, ids: Iterable[str], now: datetime | None = None) -> bool:
    """True when ``ids`` is non-empty and every id was in an emergency notification in the last 24 h."""
    ids = {str(i) for i in ids if i}
    return bool(ids) and ids <= notified_ids(db, user_id, now=now)


def handle_red_flags(db: Session, user: User, labels: list[str], source: str, source_id: str | None,
                     language: str | None = None, observations: list[Observation] | None = None,
                     extra: dict[str, Any] | None = None, *, assess: bool = True) -> str | None:
    """Emergency guidance for ``labels`` (None when empty) + throttled notification + event assessment."""
    from app.services.guardrails import emergency_response
    from app.services.notify import emergency_number, notify_emergency

    labels = list(dict.fromkeys(labels or []))
    if not labels:
        return None
    profile = user.profile
    lang = language or (profile.language if profile else None) or "en"
    lang = "ja" if lang.lower().startswith("ja") else "en"
    guidance = emergency_response(lang, labels, emergency_number(profile.timezone if profile else None, lang))

    db.flush()
    ids = [o.id for o in observations or [] if o.id]
    if already_notified(db, user.id, ids):
        log.info("emergency notification throttled", extra={"source": source})
    else:
        try:
            notify_emergency(db, user, {"summary": f"EMG-01 red flag in {source}: {', '.join(labels)}",
                                        "fired_rules": ["EMG-01"], "red_flags": labels, "source": source,
                                        "source_id": source_id, "observation_ids": ids, **(extra or {})},
                             commit=False)
        except Exception:  # notification failure must never block the guidance
            log.exception("notify_emergency failed")
    if assess:
        try:
            from app.services.risk_service import assess_user

            assess_user(db, user, trigger="event", commit=False)
        except Exception:
            log.exception("event assessment after red flag failed")
    return guidance
