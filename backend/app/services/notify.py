"""In-app notifications (+ optional email).

    notify(db, user.id, KIND_RISK_LEVEL_CHANGE, "...", "...", payload={...}, email=True)

Email is only sent when the user has an email address AND an active
`data_processing` consent. Every call is audited.
"""

from __future__ import annotations

import html as _html
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import audit, has_consent
from app.db.models import Notification, User
from app.providers.email import Attachment, EmailProvider, get_email_provider, render_email

log = get_logger(__name__)

# ---------------------------------------------------------------- kinds
KIND_RISK_LEVEL_CHANGE = "risk_level_change"
KIND_FOLLOWUP_RECOMMENDED = "followup_recommended"
KIND_APPOINTMENT_APPROVAL_NEEDED = "appointment_approval_needed"
KIND_APPOINTMENT_REQUESTED = "appointment_requested"
KIND_APPOINTMENT_NEEDS_INFO = "appointment_needs_info"
KIND_APPOINTMENT_CONFIRMED = "appointment_confirmed"
KIND_APPOINTMENT_FAILED = "appointment_failed"
KIND_DATA_NEEDED = "data_needed"
KIND_IMPORT_FINISHED = "import_finished"
KIND_WEEKLY_SUMMARY = "weekly_summary"

NOTIFICATION_KINDS = (
    KIND_RISK_LEVEL_CHANGE, KIND_FOLLOWUP_RECOMMENDED, KIND_APPOINTMENT_APPROVAL_NEEDED,
    KIND_APPOINTMENT_REQUESTED, KIND_APPOINTMENT_NEEDS_INFO, KIND_APPOINTMENT_CONFIRMED, KIND_APPOINTMENT_FAILED,
    KIND_DATA_NEEDED, KIND_IMPORT_FINISHED,
    KIND_WEEKLY_SUMMARY,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _body_html(body: str | None) -> str:
    if not body:
        return ""
    return "".join(f"<p>{_html.escape(p)}</p>" for p in body.split("\n") if p.strip())


class EmailContent:
    """A designed e-mail a caller hands to ``notify`` instead of the default title + body rendering
    (see app/services/user_emails.py). ``attachments`` are ``app.providers.email.Attachment``."""

    __slots__ = ("subject", "html", "text", "attachments")

    def __init__(self, subject: str, html: str, text: str, attachments: list[Attachment] | None = None) -> None:
        self.subject, self.html, self.text, self.attachments = subject, html, text, list(attachments or [])


def _send(provider: EmailProvider, to: str, title: str, body: str | None, kind: str,
          content: EmailContent | None = None) -> dict[str, Any]:
    if content is None:
        content = EmailContent(title, render_email(title, _body_html(body), cta_url=settings.frontend_origin), body or "")
    try:
        result = provider.send(to, content.subject, content.html, text=content.text, tags={"kind": kind},
                               attachments=content.attachments or None)
        out = result.as_dict()
        if content.attachments:
            out["attachments"] = [a.as_dict() for a in content.attachments]
        return out
    except Exception as exc:  # never let email break the notification write
        log.warning("email send raised", extra={"error": str(exc)})
        return {"id": None, "provider": getattr(provider, "name", "?"), "accepted": False, "error": str(exc)}


# ---------------------------------------------------------------- core

def notify(
    db: Session,
    user_id: str,
    kind: str,
    title: str,
    body: str | None,
    payload: dict[str, Any] | None = None,
    email: bool = False,
    email_provider: EmailProvider | None = None,
    *,
    email_content: EmailContent | None = None,
    commit: bool = True,
) -> Notification:
    """Write a Notification row; optionally email the user (consent 'data_processing' required).

    ``email_content`` replaces the default rendering (title + body + one button) with a designed
    message. The email outcome is stored in `payload["email"]` as
    `{"sent": bool, "reason"?: str, "result"?: EmailResult.as_dict()}`.
    """
    if kind not in NOTIFICATION_KINDS:
        log.warning("unknown notification kind", extra={"kind": kind})
    data: dict[str, Any] = dict(payload or {})

    if email:
        user = db.get(User, user_id)
        if user is None or not user.email:
            data["email"] = {"sent": False, "reason": "no_email"}
        elif not has_consent(db, user_id, "data_processing"):
            data["email"] = {"sent": False, "reason": "no_consent"}
        else:
            result = _send(email_provider or get_email_provider(), user.email, title, body, kind, email_content)
            data["email"] = {"sent": bool(result["accepted"]), "result": result}

    row = Notification(user_id=user_id, kind=kind, title=title[:300], body=body, payload=data)
    db.add(row)
    db.flush()
    audit(db, user_id, "notification.create", f"notification:{row.id}",
          {"kind": kind, "email": data.get("email", {}).get("sent", False)}, actor="system")
    if commit:
        db.commit()
    return row


# ---------------------------------------------------------------- queries

def list_notifications(db: Session, user_id: str, unread_only: bool = False, limit: int = 50) -> list[Notification]:
    """Newest first, scoped to the user."""
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(max(1, min(limit, 200)))
    return list(db.scalars(stmt))


def unread_count(db: Session, user_id: str) -> int:
    return db.scalar(
        select(func.count(Notification.id)).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    ) or 0


def mark_read(db: Session, user_id: str, notification_id: str) -> Notification | None:
    """Mark one of the user's notifications read (idempotent). None if not found / not theirs."""
    row = db.get(Notification, notification_id)
    if row is None or row.user_id != user_id:
        return None
    if row.read_at is None:
        row.read_at = _utcnow()
        db.commit()
    return row


def mark_all_read(db: Session, user_id: str) -> int:
    """Mark every unread notification of the user read. Returns the count updated."""
    res = db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=_utcnow())
    )
    db.commit()
    return res.rowcount or 0
