"""E-mail channel (booking-v2 spec §1.6 "email").

Outbound: one Resend message per attempt to `sandbox.resolve_target("email", clinic)`.
  From      booking@<BOOKING_REPLY_DOMAIN> when the domain is set, else RESEND_FROM_EMAIL
  Reply-To  book+<reply_token>@<BOOKING_REPLY_DOMAIN> when the domain is set (inbound replies are
            matched by the token, app/services/booking/inbound.py), else the patient's e-mail
  Body      fixed template (app/templates/booking_email.html.j2): patient name, date of birth,
            phone, e-mail, postal code, generic reason category, report link, 3 slots with
            confirm links. In sandbox mode the subject is prefixed "[SANDBOX]" and the body names
            the real clinic address it was redirected from.
Result: `requested` (provider accepted) or `failed` (rejected / bounced at send time).

`send_followup(...)` sends a reply in the same thread (counter-offer acceptance, the patient's
answer to a clinic question).
"""

from __future__ import annotations

import secrets
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations import Clinic
from app.services.booking import sandbox
from app.services.booking.channels.base import BookingContext, ChannelMessage, ChannelResult
from app.services.booking.common import clinic_note, render_booking_email, utcnow

log = get_logger(__name__)


def ensure_reply_token(appt: Any) -> str:
    if not appt.reply_token:
        appt.reply_token = secrets.token_hex(8)
    return appt.reply_token


def reply_address(token: str | None) -> str | None:
    domain = settings.booking_reply_domain.strip().lower()
    return f"book+{token}@{domain}" if domain and token else None


def sender_address() -> str | None:
    domain = settings.booking_reply_domain.strip().lower()
    return f"booking@{domain}" if domain else (settings.resend_from_email or None)


def booking_sender(provider: Any) -> Any:
    """The clinic-facing provider: a ResendEmail re-bound to booking@<BOOKING_REPLY_DOMAIN> when the
    reply domain is set (so replies land on the inbound domain); any other provider unchanged."""
    from app.providers.email import ResendEmail

    domain = settings.booking_reply_domain.strip()
    if isinstance(provider, ResendEmail) and domain:
        return ResendEmail(api_key=provider.api_key, from_email=f"Health Advocate Booking <booking@{domain}>")
    return provider


class EmailChannel:
    name = "email"

    def configured(self) -> bool:
        return True  # FakeEmail in dev; ResendEmail when RESEND_API_KEY is set

    def available(self, clinic: Clinic, ctx: BookingContext) -> str | None:
        if sandbox.resolve_target("email", clinic) is None:
            return sandbox.missing_reason("email", clinic)
        return None

    def execute(self, appt: Any, ctx: BookingContext) -> ChannelResult:
        target = sandbox.resolve_target("email", ctx.clinic)
        if target is None:
            return ChannelResult.skipped(sandbox.missing_reason("email", ctx.clinic))
        token = ensure_reply_token(appt)
        reply_to = reply_address(token) or ctx.user.email
        note = clinic_note(ctx.pathway, "en", ctx.providers.get("slot_preference"))
        subject, html, text = render_booking_email(
            appointment_id=appt.id, clinic_name=ctx.clinic.name, patient=ctx.patient, note=note, slots=ctx.slots,
            tz=ctx.clinic_tz, reply_to=reply_to, reminder=ctx.reminder, sandbox_note=target.note,
            clinic_id=ctx.clinic.id)
        provider = booking_sender(ctx.email)
        kind = "booking_reminder" if ctx.reminder else "booking_request"
        result = provider.send(target.value, subject, html, text=text, reply_to=reply_to,
                               tags={"kind": kind, "appointment_id": appt.id})
        sent = {"to": target.value, "real_to": target.real, "redirected": target.redirected, "subject": subject,
                "email_id": result.id, "accepted": bool(result.accepted), "error": result.error,
                "reply_to": reply_to, "note": note, "report_url": ctx.report_url, "sent_at": utcnow().isoformat()}
        msg = ChannelMessage(direction="out", channel="email", sender=sender_address(), recipient=target.value,
                             subject=subject, body=text, external_id=f"email:{result.id}" if result.id else None)
        if not result.accepted:
            return ChannelResult.failed(f"e-mail not accepted: {result.error}", messages=[msg],
                                        target=target.as_dict(), details={"request": sent})
        return ChannelResult(status="requested", reason=target.note or "e-mail sent", external_ref=result.id,
                             messages=[msg], target=target.as_dict(), details={"request": sent})


def send_followup(appt: Any, provider: Any, user: Any, body: str, *, kind: str = "booking_reply",
                  thread: dict[str, Any] | None = None) -> tuple[bool, ChannelMessage | None, str | None]:
    """Reply to the clinic in the e-mail request's thread (`thread`, default the e-mail request of
    the current clinic) → (accepted, message, error)."""
    clinic = appt.clinic or {}
    request = thread or ((clinic.get("requests") or {}).get("email")) or clinic.get("request") or {}
    to = request.get("to")
    if not to:
        return False, None, "no e-mail thread for this appointment"
    reply_to = reply_address(ensure_reply_token(appt)) or user.email
    subject = "Re: " + (request.get("subject") or "Appointment request")
    html = "".join(f"<p>{_esc(p)}</p>" for p in body.split("\n") if p.strip())
    res = booking_sender(provider).send(to, subject, html, text=body, reply_to=reply_to,
                                        tags={"kind": kind, "appointment_id": appt.id})
    msg = ChannelMessage(direction="out", channel="email", sender=sender_address(), recipient=to, subject=subject,
                         body=body, external_id=f"email:{res.id}" if res.id else None)
    return bool(res.accepted), msg, res.error


def _esc(s: str) -> str:
    import html

    return html.escape(s)
