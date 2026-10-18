"""Email provider: Resend in the cloud, an in-memory fake for tests/dev.

    provider = get_email_provider()
    result = provider.send("user@example.com", "Subject", render_email("Title", "<p>Body</p>"))

`get_email_provider()` returns `FakeEmail` when RESEND_API_KEY is empty or when
`settings.email_provider == "fake"` (optional setting). Sending never raises for
delivery problems: the `EmailResult` carries `accepted=False` and `error`.
Only a misconfigured `ResendEmail` (no API key / no from address) raises at construction.
"""

from __future__ import annotations

import base64
import html as _html
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Canada only, English only (BACKEND_PLAN revision 2026-10-17). The Japanese text is kept for
# callers that still import it; e-mails render the English disclaimer only.
DISCLAIMER_JA = (
    "これは診断ではありません。本サービスは医療機関の受診を補助するものであり、"
    "医師による診察・診断の代わりにはなりません。"
)
DISCLAIMER_EN = (
    "This is not a diagnosis. This service helps you prepare for a medical visit and does not "
    "replace examination or diagnosis by a doctor. For health advice, "
    "call 811 to speak with a registered nurse."
)


@dataclass
class EmailResult:
    """Outcome of a send attempt."""

    id: str | None
    provider: str
    accepted: bool
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "provider": self.provider, "accepted": self.accepted, "error": self.error}


@dataclass
class Attachment:
    """A file sent with the e-mail (e.g. the .ics invitation of a confirmed appointment)."""

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"

    def as_dict(self) -> dict[str, Any]:
        return {"filename": self.filename, "content_type": self.content_type, "size": len(self.content)}


class EmailProvider(Protocol):
    name: str

    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        text: str | None = None,
        reply_to: str | None = None,
        tags: dict[str, str] | None = None,
        attachments: list[Attachment] | None = None,
    ) -> EmailResult: ...


def _as_list(to: str | list[str]) -> list[str]:
    return [to] if isinstance(to, str) else list(to)


@dataclass
class SentEmail:
    """One message captured by FakeEmail."""

    id: str
    to: list[str]
    subject: str
    html: str
    text: str | None = None
    reply_to: str | None = None
    tags: dict[str, str] | None = None
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class FakeEmail:
    """Offline provider: stores messages in `.outbox` and logs them."""

    name: str = "fake"
    outbox: list[SentEmail] = field(default_factory=list)

    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        text: str | None = None,
        reply_to: str | None = None,
        tags: dict[str, str] | None = None,
        attachments: list[Attachment] | None = None,
    ) -> EmailResult:
        msg = SentEmail(
            id=f"fake_{uuid.uuid4().hex[:12]}", to=_as_list(to), subject=subject, html=html,
            text=text, reply_to=reply_to, tags=tags, attachments=list(attachments or []),
        )
        self.outbox.append(msg)
        if not settings.dev_mode:
            # never report a fake send as delivered in production (review fix 9)
            log.warning("fake email provider used in production; message not delivered",
                        extra={"email_id": msg.id, "subject": subject})
            return EmailResult(id=msg.id, provider=self.name, accepted=False, error="fake provider in production")
        log.info("fake email sent", extra={"email_id": msg.id, "to": msg.to, "subject": subject})
        return EmailResult(id=msg.id, provider=self.name, accepted=True)


class ResendEmail:
    """Resend (https://resend.com) provider. `resend` is imported lazily."""

    name = "resend"

    def __init__(self, api_key: str | None = None, from_email: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else settings.resend_api_key
        self.from_email = from_email if from_email is not None else settings.resend_from_email
        if not self.api_key:
            raise RuntimeError("ResendEmail requires RESEND_API_KEY to be set")
        if not self.from_email:
            raise RuntimeError("ResendEmail requires RESEND_FROM_EMAIL to be set")

    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        text: str | None = None,
        reply_to: str | None = None,
        tags: dict[str, str] | None = None,
        attachments: list[Attachment] | None = None,
    ) -> EmailResult:
        try:
            import resend
        except ImportError as exc:  # pragma: no cover - dependency is in requirements
            return EmailResult(id=None, provider=self.name, accepted=False, error=f"resend not installed: {exc}")

        resend.api_key = self.api_key
        params: dict[str, Any] = {"from": self.from_email, "to": _as_list(to), "subject": subject, "html": html}
        if text:
            params["text"] = text
        if reply_to:
            params["reply_to"] = reply_to
        if tags:
            params["tags"] = [{"name": k, "value": str(v)} for k, v in tags.items()]
        if attachments:
            params["attachments"] = [{"filename": a.filename, "content_type": a.content_type,
                                      "content": base64.b64encode(a.content).decode("ascii")} for a in attachments]
        try:
            resp = resend.Emails.send(params)
        except Exception as exc:  # network / API errors are reported, not raised
            log.warning("resend send failed", extra={"error": str(exc)})
            return EmailResult(id=None, provider=self.name, accepted=False, error=str(exc))
        email_id = resp.get("id") if isinstance(resp, dict) else getattr(resp, "id", None)
        return EmailResult(id=email_id, provider=self.name, accepted=bool(email_id),
                           error=None if email_id else "no id returned")


def get_email_provider() -> EmailProvider:
    """FakeEmail when no API key / from address, or when settings.email_provider == 'fake'."""
    if getattr(settings, "email_provider", None) == "fake" or not settings.resend_api_key or not settings.resend_from_email:
        return FakeEmail()
    return ResendEmail()


BRAND_COLOR = "#155bd0"      # the frontend's --primary
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _button(url: str, label: str, primary: bool = True) -> str:
    style = (f"background:{BRAND_COLOR};color:#ffffff;" if primary
             else "background:#eef1f5;color:#1d1d1f;")
    return (f'<a href="{_html.escape(url, quote=True)}" style="display:inline-block;{style}padding:12px 20px;'
            'border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;margin:0 8px 8px 0">'
            f"{_html.escape(label)}</a>")


def email_shell(title: str, body_html: str, *, eyebrow: str | None = None, buttons: list[tuple[str, str, bool]] | None = None,
                footer_note: str | None = None, preheader: str | None = None) -> str:
    """The shared layout of every e-mail the product sends to a USER (clinic e-mails have their own
    template): brand header, title, trusted `body_html`, optional buttons `[(url, label, primary)]`,
    the Canadian disclaimer footer. `title`, `eyebrow`, `footer_note`, `preheader` are escaped."""
    from app.core.config import settings as _settings

    brand = _html.escape(_settings.product_name)
    t = _html.escape(title)
    pre = (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{_html.escape(preheader)}</div>'
           if preheader else "")
    eyebrow_html = (f'<p style="margin:0 0 8px;font-size:12px;font-weight:700;letter-spacing:.06em;'
                    f'text-transform:uppercase;color:{BRAND_COLOR}">{_html.escape(eyebrow)}</p>' if eyebrow else "")
    btns = ("".join(_button(u, l, p) for u, l, p in buttons) if buttons else "")
    btn_block = f'<div style="margin:24px 0 8px">{btns}</div>' if btns else ""
    note = (f'<p style="margin:16px 0 0;font-size:13px;color:#6e6e73">{_html.escape(footer_note)}</p>'
            if footer_note else "")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{t}</title></head>"
        f'<body style="margin:0;padding:0;background:#f5f5f7;font-family:{_FONT};color:#1d1d1f">{pre}'
        '<div style="max-width:600px;margin:0 auto;padding:24px 16px">'
        f'<p style="margin:0 0 16px;font-size:15px;font-weight:700;color:{BRAND_COLOR}">{brand}</p>'
        '<div style="background:#ffffff;border-radius:12px;padding:28px 24px;border:1px solid #e3e3e8">'
        f"{eyebrow_html}"
        f'<h1 style="margin:0 0 16px;font-size:22px;line-height:1.25;font-weight:700">{t}</h1>'
        f'<div style="font-size:15px;line-height:1.5">{body_html}</div>'
        f"{btn_block}{note}"
        "</div>"
        f'<p style="margin:20px 8px 0;font-size:12px;line-height:1.5;color:#6e6e73">{_html.escape(DISCLAIMER_EN)}</p>'
        f'<p style="margin:8px 8px 0;font-size:12px;color:#6e6e73">You receive this e-mail because notifications '
        f"are switched on for your {brand} account.</p>"
        "</div></body></html>"
    )


def render_email(title: str, body_html: str, cta_url: str | None = None, cta_label: str = "Open CareSync") -> str:
    """A plain notification e-mail: title + trusted `body_html` + one button + the disclaimer footer.
    `title` and `cta_url` are escaped. (Appointment e-mails use app/services/user_emails.py.)"""
    return email_shell(title, body_html, buttons=[(cta_url, cta_label, True)] if cta_url else None)
