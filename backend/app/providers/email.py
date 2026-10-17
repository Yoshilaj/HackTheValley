"""Email provider: Resend in the cloud, an in-memory fake for tests/dev.

    provider = get_email_provider()
    result = provider.send("user@example.com", "Subject", render_email("Title", "<p>Body</p>"))

`get_email_provider()` returns `FakeEmail` when RESEND_API_KEY is empty or when
`settings.email_provider == "fake"` (optional setting). Sending never raises for
delivery problems: the `EmailResult` carries `accepted=False` and `error`.
Only a misconfigured `ResendEmail` (no API key / no from address) raises at construction.
"""

from __future__ import annotations

import html as _html
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

DISCLAIMER_JA = (
    "これは診断ではありません。本サービスは医療機関の受診を補助するものであり、"
    "医師による診察・診断の代わりにはなりません。緊急の場合は119番に電話してください。"
)
DISCLAIMER_EN = (
    "This is not a diagnosis. This service helps you prepare for a medical visit and does not "
    "replace examination or diagnosis by a doctor. In an emergency, call your local emergency number "
    "(119 in Japan)."
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
    ) -> EmailResult:
        msg = SentEmail(
            id=f"fake_{uuid.uuid4().hex[:12]}", to=_as_list(to), subject=subject, html=html,
            text=text, reply_to=reply_to, tags=tags,
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


def render_email(title: str, body_html: str, cta_url: str | None = None, cta_label: str = "Open / 開く") -> str:
    """Minimal inline-styled HTML email with the mandatory JA + EN disclaimer footer.
    `body_html` is trusted HTML built by the caller; `title` and `cta_url` are escaped."""
    t = _html.escape(title)
    cta = ""
    if cta_url:
        cta = (
            f'<p style="margin:24px 0"><a href="{_html.escape(cta_url, quote=True)}" '
            'style="background:#2563eb;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">'
            f"{_html.escape(cta_label)}</a></p>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>" + t + "</title></head>"
        '<body style="font-family:-apple-system,Helvetica,Arial,sans-serif;color:#111;max-width:600px;margin:0 auto;padding:16px">'
        f'<h1 style="font-size:20px">{t}</h1>'
        f"<div>{body_html}</div>{cta}"
        '<hr style="border:none;border-top:1px solid #ddd;margin:24px 0">'
        f'<p style="font-size:12px;color:#555">{_html.escape(DISCLAIMER_JA)}</p>'
        f'<p style="font-size:12px;color:#555">{_html.escape(DISCLAIMER_EN)}</p>'
        "</body></html>"
    )
