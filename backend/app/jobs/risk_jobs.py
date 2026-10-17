"""Nightly risk assessment job (BACKEND_PLAN C5)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import User
from app.jobs.registry import HOOK_NIGHTLY_RISK_ASSESSMENT, job

log = get_logger(__name__)


@job(HOOK_NIGHTLY_RISK_ASSESSMENT, cron="0 3 * * *")
def nightly_risk_assessment(db: Session) -> dict[str, Any]:
    """Run ``assess_user(trigger='nightly')`` for every user; one failing user never stops the rest."""
    from app.services.risk_service import assess_user

    counts: dict[str, Any] = {"users": 0, "assessed": 0, "failed": 0, "levels": {}}
    for user_id in list(db.scalars(select(User.id))):
        counts["users"] += 1
        try:
            user = db.get(User, user_id)
            for row in assess_user(db, user, trigger="nightly"):
                counts["levels"][row.level] = counts["levels"].get(row.level, 0) + 1
            counts["assessed"] += 1
        except Exception as exc:
            db.rollback()
            counts["failed"] += 1
            log.exception("nightly assessment failed", extra={"user_id": user_id, "error": str(exc)})
    return counts
