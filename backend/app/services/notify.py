"""In-app notifications (+ optional email) and the emergency notification path.

    notify(db, user.id, KIND_RISK_LEVEL_CHANGE, "...", "...", payload={...}, email=True)
    notify_emergency(db, user, "EMG-01: chest pain reported in chat")

Email is only sent when the user has an email address AND an active
`data_processing` consent; the emergency contact is only emailed with an active
`emergency_contact` consent (spec phase-a §7). Every call is audited.
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
from app.providers.email import EmailProvider, get_email_provider, render_email

log = get_logger(__name__)

# ---------------------------------------------------------------- kinds
KIND_RISK_LEVEL_CHANGE = "risk_level_change"
KIND_FOLLOWUP_RECOMMENDED = "followup_recommended"
KIND_EMERGENCY = "emergency"
KIND_APPOINTMENT_APPROVAL_NEEDED = "appointment_approval_needed"
KIND_APPOINTMENT_REQUESTED = "appointment_requested"
KIND_APPOINTMENT_NEEDS_INFO = "appointment_needs_info"
KIND_APPOINTMENT_CONFIRMED = "appointment_confirmed"
KIND_APPOINTMENT_FAILED = "appointment_failed"
KIND_DATA_NEEDED = "data_needed"
KIND_IMPORT_FINISHED = "import_finished"
KIND_WEEKLY_SUMMARY = "weekly_summary"

NOTIFICATION_KINDS = (
    KIND_RISK_LEVEL_CHANGE, KIND_FOLLOWUP_RECOMMENDED, KIND_EMERGENCY, KIND_APPOINTMENT_APPROVAL_NEEDED,
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


def _send(provider: EmailProvider, to: str, title: str, body: str | None, kind: str) -> dict[str, Any]:
    html = render_email(title, _body_html(body), cta_url=f"{settings.frontend_origin}/notifications")
    try:
        result = provider.send(to, title, html, text=body, tags={"kind": kind})
        return result.as_dict()
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
    commit: bool = True,
) -> Notification:
    """Write a Notification row; optionally email the user (consent 'data_processing' required).

    The email outcome is stored in `payload["email"]` as
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
            result = _send(email_provider or get_email_provider(), user.email, title, body, kind)
            data["email"] = {"sent": bool(result["accepted"]), "result": result}

    row = Notification(user_id=user_id, kind=kind, title=title[:300], body=body, payload=data)
    db.add(row)
    db.flush()
    audit(db, user_id, "notification.create", f"notification:{row.id}",
          {"kind": kind, "email": data.get("email", {}).get("sent", False)}, actor="system")
    if commit:
        db.commit()
    return row


# ---------------------------------------------------------------- emergency (spec §7)

def emergency_number(timezone_name: str | None = None, language: str | None = None) -> str:
    """Emergency number for the user. The product is Canada-only, so this is always
    ``settings.emergency_number`` (911, which also works in the US). The arguments are kept for
    call-site compatibility; the old timezone → number map (119 for Japan, ...) was removed."""
    return settings.emergency_number


def emergency_guidance(language: str | None, number: str) -> tuple[str, str]:
    """(title, body) emergency guidance — English by default; Japanese only when explicitly requested."""
    health = settings.health_line_number
    if (language or "en").lower().startswith("ja"):
        return (
            f"緊急: すぐに{number}番に電話してください",
            f"胸の痛み・顔のゆがみ・腕の力が入らない・言葉が出にくい・失神・突然の激しい頭痛などがある場合は、"
            f"ためらわずに今すぐ{number}番に電話してください。\n"
            f"緊急かどうか迷う場合は{health}（看護師による電話健康相談）に相談できます。\n"
            "このアプリは診断を行いません。予約の手配は行わず、緊急対応を優先してください。",
        )
    return (
        f"Emergency: call {number} now",
        f"If you have chest pain or pressure, face drooping, arm weakness, speech difficulty, fainting, "
        f"or a sudden severe headache, call {number} immediately.\n"
        f"If you are unsure whether it is an emergency, call {health} to speak with a registered nurse "
        "(Health Link / Health811, most provinces and territories). For thoughts of suicide, call or text 9-8-8.\n"
        "Do not wait for an appointment. This app does not diagnose; please seek emergency care first.",
    )


def notify_emergency(
    db: Session,
    user: User,
    assessment_summary: str | dict[str, Any] | None,
    email_provider: EmailProvider | None = None,
    *,
    commit: bool = True,
) -> Notification:
    """Emergency path (spec §7): write an `emergency` notification with guidance (911 / 811 / 9-8-8,
    Canada; ``settings.emergency_number``), email the user (consent-gated by notify) and,
    with an active 'emergency_contact' consent and profile.emergency_contact.email, the contact.
    Never triggers booking. Returns the Notification (payload has `emergency_number`, `contact_email`,
    `observation_ids`). ``commit=False`` leaves the transaction to the caller (e.g. ``assess_user``)."""
    profile = user.profile
    language = (profile.language if profile else None) or "en"
    number = emergency_number(profile.timezone if profile else None, language)
    title, body = emergency_guidance(language, number)

    summary = assessment_summary
    if isinstance(summary, dict):
        summary_text = summary.get("summary") or ", ".join(map(str, summary.get("fired_rules") or [])) or None
    else:
        summary_text = summary
    provider = email_provider or get_email_provider()

    contact_info: dict[str, Any]
    contact = (profile.emergency_contact if profile else None) or {}
    if not has_consent(db, user.id, "emergency_contact"):
        contact_info = {"sent": False, "reason": "no_consent"}
    elif not contact.get("email"):
        contact_info = {"sent": False, "reason": "no_contact_email"}
    else:
        name = (profile.display_name if profile else None) or user.email
        if (language or "en").lower().startswith("ja"):
            c_title = f"【緊急】{name} さんに緊急のサインがあります"
            c_body = (
                f"{name} さんの健康記録アプリで緊急の可能性があるサインが検出されました。\n"
                f"{summary_text or ''}\n"
                f"至急ご本人に連絡を取り、必要であれば{number}番に通報してください。"
            )
        else:
            c_title = f"Urgent: possible emergency signs for {name}"
            c_body = (
                f"{name}'s health tracking app detected possible emergency warning signs.\n"
                f"{summary_text or ''}\n"
                f"Please contact them right away and call {number} if needed."
            )
        result = _send(provider, contact["email"], c_title, c_body, KIND_EMERGENCY)
        contact_info = {"sent": bool(result["accepted"]), "result": result}
        audit(db, user.id, "emergency_contact.email", "profile:emergency_contact",
              {"accepted": result["accepted"]}, actor="system")

    payload = {
        "emergency_number": number,
        "summary": summary_text,
        "assessment": assessment_summary if isinstance(assessment_summary, dict) else None,
        "contact_email": contact_info,
        # trigger ids (red-flag observation ids / evidence refs) — used to throttle repeats (§7)
        "observation_ids": list((assessment_summary or {}).get("observation_ids") or [])
        if isinstance(assessment_summary, dict) else [],
    }
    return notify(db, user.id, KIND_EMERGENCY, title, body, payload=payload, email=True, email_provider=provider,
                  commit=commit)


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
