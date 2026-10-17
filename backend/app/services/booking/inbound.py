"""Inbound clinic replies via Resend Receiving (booking-v2 spec §1.6 "email").

    verify_svix(secret, svix_id, svix_timestamp, svix_signature, raw_body)   # raises WebhookVerificationError
    result = handle_resend_event(db, payload, llm=None, fetcher=None)       # {"status": …}

Resend inbound findings (docs checked 2026-10-17: resend.com/docs/webhooks/emails/received,
/docs/api-reference/emails/retrieve-received-email, /docs/webhooks/verify-webhooks-requests,
docs.svix.com/receiving/verifying-payloads/how-manual):
- Event type for a received e-mail is ``email.received``. Payload:
  ``{"type": "email.received", "created_at": …, "data": {"email_id", "created_at", "from", "to": [],
  "cc": [], "bcc": [], "received_for": [], "message_id", "subject", "attachments": [{id, filename,
  content_type, content_disposition, content_id}]}}`` — metadata ONLY: no html / text / headers.
- The body must be fetched: ``GET https://api.resend.com/emails/receiving/{email_id}`` with
  ``Authorization: Bearer RESEND_API_KEY`` (Python SDK ``resend.Emails.Receiving.get(email_id=…)``)
  → ``{id, from, to, subject, html, text, headers: {from: "Name <addr>", …}, reply_to, received_for,
  message_id, raw: {download_url, expires_at}, attachments: [...]}``. `data.from` in the webhook is
  the bare address; the display name is in ``headers.from`` of the retrieve response.
- Webhooks are signed by Svix: headers ``svix-id``, ``svix-timestamp``, ``svix-signature``
  (space-separated ``v1,<base64>`` entries). Signed content = ``f"{svix_id}.{svix_timestamp}.{raw_body}"``,
  HMAC-SHA256 with the base64-decoded part of the ``whsec_…`` secret; compare in constant time; we
  reject timestamps more than 5 minutes off. The raw body must be used (no JSON re-serialisation).
Dev/test convenience: when `data` already carries ``text`` / ``html`` no API call is made.

Matching (on the webhook metadata, before any body is fetched / parsed): the token in
``book+<token>@<BOOKING_REPLY_DOMAIN>`` among to / cc / received_for → ``Appointment.reply_token`` —
the only match that may change an appointment. Fallback `sender_review`: the sender is the clinic
address of exactly ONE open appointment and not the shared sandbox / fallback inbox → the message is
stored as ``unverified`` and the user is asked to review it (no state change). Idempotent on the Resend
``email_id`` (AppointmentMessage.external_id ``email-in:<id>``). Classification: ``llm.extract`` with
``prompts.booking.CLASSIFY_SCHEMA``; when the provider returns no valid outcome (FakeLlm, errors) a
deterministic keyword / date parser (`heuristic_classify`) is used so the flow never stalls.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Appointment, AppointmentMessage
from app.integrations import Slot
from app.prompts import booking as booking_prompt
from app.services.booking.channels.base import ChannelMessage
from app.services.booking.common import slot_label, tz_of
from app.services.booking.slots import clinic_timezone

log = get_logger(__name__)

RECEIVING_URL = "https://api.resend.com/emails/receiving/{id}"
TOLERANCE_S = 300
OUTCOMES = ("confirmed", "counter_offer", "needs_info", "declined", "other")
TOKEN_RE = re.compile(r"book\+([a-z0-9]{6,64})@", re.I)
ADDR_RE = re.compile(r"<([^>]+)>")


class WebhookVerificationError(Exception):
    pass


# ================================================================ Svix signature

def verify_svix(secret: str, svix_id: str | None, svix_timestamp: str | None, svix_signature: str | None,
                body: bytes, now: float | None = None, tolerance: int = TOLERANCE_S) -> None:
    if not (svix_id and svix_timestamp and svix_signature):
        raise WebhookVerificationError("missing svix headers")
    try:
        ts = int(svix_timestamp)
    except ValueError as exc:
        raise WebhookVerificationError("bad timestamp") from exc
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        raise WebhookVerificationError("timestamp outside tolerance")
    raw = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw)
    except Exception as exc:
        raise WebhookVerificationError("bad secret") from exc
    signed = f"{svix_id}.{svix_timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in svix_signature.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return
    raise WebhookVerificationError("no matching signature")


def sign_svix(secret: str, svix_id: str, svix_timestamp: str, body: bytes) -> str:
    """Test/dev helper: the `svix-signature` header value for `body`."""
    raw = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    mac = hmac.new(base64.b64decode(raw), f"{svix_id}.{svix_timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


# ================================================================ body fetch + text cleanup

def fetch_received_email(email_id: str) -> dict[str, Any]:
    """GET /emails/receiving/{id} (Resend API). Raises on HTTP errors / missing key."""
    import httpx

    if not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY not configured; cannot fetch the received e-mail body")
    resp = httpx.get(RECEIVING_URL.format(id=email_id), timeout=15,
                     headers={"Authorization": f"Bearer {settings.resend_api_key}"})
    resp.raise_for_status()
    return resp.json()


MAX_HTML = 200_000


class _TextParser(HTMLParser):
    """Linear-time HTML → text (stdlib parser; script / style / head content dropped)."""
    BREAKS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
    SKIP = {"script", "style", "head", "title", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BREAKS:
            self.out.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BREAKS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BREAKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str | None) -> str:
    """Plain text of an e-mail's HTML part: capped at MAX_HTML characters, parsed with the stdlib
    HTMLParser (no backtracking regex, so a huge unclosed <script> cannot stall the webhook)."""
    if not html:
        return ""
    p = _TextParser()
    try:
        p.feed(html[:MAX_HTML])
        p.close()
    except Exception:  # malformed markup: keep what was parsed
        pass
    text = "".join(p.out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r" ?\n[ \n]*", "\n", text).strip()


def strip_quoted(text: str) -> str:
    """Drop the quoted original (``> …`` lines, "On … wrote:", "-----Original Message-----")."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if re.match(r"^(on .+ wrote:|-{2,}\s*original message\s*-{2,}|from:\s.+@.+|_{5,})$", s, re.I):
            break
        if s.startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def bare_address(value: str | None) -> str:
    v = value or ""
    m = ADDR_RE.search(v)
    return (m.group(1) if m else v).strip().lower()


# ================================================================ classification

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], start=1)}
_ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3}
DECLINE = re.compile(r"unfortunately|not accepting|no longer accept|cannot (book|accept|see|offer)|can't (book|accept|see)|"
                     r"unable to (book|accept|see|offer)|we do not (book|accept)|referral (is )?(required|needed)|"
                     r"need a referral|declin", re.I)
CONFIRM = re.compile(r"confirm|booked|you'?re (all )?set|is set|see you|reserved|scheduled you|we have you", re.I)
COUNTER = re.compile(r"instead|how about|would .{0,40}work|alternative|another time|we can offer|we could offer|"
                     r"earliest|next available|available (on|at)|offer you", re.I)
QUESTION = re.compile(r"please (send|provide|let us know|confirm (your|whether|if))|could you|can you|do you have|"
                      r"we need|we require|is the patient|has the patient|what is", re.I)


def _slot_from_text(text: str, proposed: list[Slot], tz: str, now: datetime | None = None) -> tuple[Slot | None, bool]:
    """(slot, is_one_of_proposed) from a reply."""
    zone = ZoneInfo(tz)
    low = text.lower()
    m = re.search(r"\b(?:option|slot|time|choice|number|no\.?|#)\s*#?([1-3])\b", low)
    if m and proposed:
        i = int(m.group(1)) - 1
        if i < len(proposed):
            return proposed[i], True
    m = re.search(r"\bthe (first|second|third|1st|2nd|3rd)\b", low)
    if m and proposed and _ORD[m.group(1)] <= len(proposed):
        return proposed[_ORD[m.group(1)] - 1], True
    m = re.search(r"(\d{4}-\d{2}-\d{2})[t ](\d{1,2}):(\d{2})", low)
    found: datetime | None = None
    if m:
        d = datetime.fromisoformat(m.group(1))
        found = d.replace(hour=int(m.group(2)), minute=int(m.group(3)), tzinfo=zone)
    else:
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                      r"(?:,?\s*(\d{4}))?(?:,?\s*(?:at|@|from)?\s*(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?)?", low)
        if m:
            now_l = (now or datetime.now(zone)).astimezone(zone)
            year = int(m.group(3)) if m.group(3) else now_l.year
            month, day = _MONTHS[m.group(1)], int(m.group(2))
            if m.group(4):
                hour, minute = int(m.group(4)), int(m.group(5) or 0)
                ampm = (m.group(6) or "").replace(".", "")
                if ampm == "pm" and hour < 12:
                    hour += 12
                if ampm == "am" and hour == 12:
                    hour = 0
                if not ampm and hour < 7:
                    hour += 12  # "at 3" in a clinic context means 15:00
                try:
                    found = datetime(year, month, day, hour, minute, tzinfo=zone)
                except ValueError:
                    found = None
                if found is not None and not m.group(3) and found < now_l - timedelta(days=1):
                    found = found.replace(year=year + 1)
            else:  # a date only: match a proposed slot on that day
                for s in proposed:
                    local = s.start.astimezone(zone)
                    if (local.month, local.day) == (month, day):
                        return s, True
    if found is None:
        return None, False
    for s in proposed:
        if s.start == found:
            return s, True
    return Slot(found, found + timedelta(minutes=30)), False


def heuristic_classify(text: str, proposed: list[Slot], tz: str, now: datetime | None = None) -> dict[str, Any]:
    slot, in_proposed = _slot_from_text(text, proposed, tz, now)
    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+|\n+", text) if s.strip()]
    question = next((s for s in sentences if s.endswith("?")), None) or \
        next((s for s in sentences if QUESTION.search(s)), None)
    summary = (sentences[0] if sentences else "")[:200]
    if DECLINE.search(text) and not (slot and CONFIRM.search(text)):
        outcome = "declined"
    elif slot and COUNTER.search(text) and not (in_proposed and CONFIRM.search(text)):
        outcome = "counter_offer"
    elif slot and (CONFIRM.search(text) or in_proposed):
        outcome = "confirmed"
    elif question:
        outcome = "needs_info"
    elif slot:
        outcome = "counter_offer"
    else:
        outcome = "other"
    return {"outcome": outcome, "slot": slot.start.isoformat() if slot and outcome in ("confirmed", "counter_offer") else "",
            "requested_info": (question or "")[:300] if outcome == "needs_info" else "", "summary": summary,
            "method": "heuristic"}


def classify_reply(llm: Any, text: str, proposed: list[Slot], tz: str, *, audit_hook: Any = None,
                   now: datetime | None = None) -> dict[str, Any]:
    """LLM classification (any LlmProvider) with the deterministic fallback."""
    raw: dict[str, Any] = {}
    if llm is not None:
        try:
            raw = llm.extract(booking_prompt.classify_system(tz, [f"{i + 1}. {s.start.isoformat()} ({slot_label(s, tz)})"
                                                                for i, s in enumerate(proposed)]),
                              text[:8000], booking_prompt.CLASSIFY_SCHEMA, purpose="booking_reply_classify",
                              audit_hook=audit_hook) or {}
        except Exception as exc:
            log.warning("reply classification failed; using heuristic", extra={"error": str(exc)})
            raw = {}
    heur = heuristic_classify(text, proposed, tz, now)
    if raw.get("outcome") not in OUTCOMES:
        return heur
    out = {"outcome": raw["outcome"], "slot": str(raw.get("slot") or ""),
           "requested_info": str(raw.get("requested_info") or "")[:300],
           "summary": str(raw.get("summary") or heur["summary"])[:300], "method": "llm"}
    if out["outcome"] in ("confirmed", "counter_offer") and not out["slot"]:
        out["slot"] = heur["slot"]
    return out


# ================================================================ webhook handling

def _match(db: Session, recipients: list[str], sender: str) -> tuple[Appointment | None, str | None]:
    """(appointment, how). `reply_token` (book+<token>@ in to / cc / received_for) is the only proof
    that a message belongs to an appointment. `sender_review`: no token, but the sender is the
    clinic address of exactly ONE open appointment and not a shared sandbox / fallback inbox — the
    message is stored for the user to review and never changes the appointment."""
    for r in recipients:
        m = TOKEN_RE.search(r or "")
        if m:
            appt = db.scalar(select(Appointment).where(Appointment.reply_token == m.group(1).lower()))
            if appt is not None:
                return appt, "reply_token"
    shared = {a.strip().lower() for a in (settings.sandbox_clinic_email, settings.clinic_fallback_email) if a}
    if not sender or sender in shared:
        return None, None
    open_rows = db.scalars(select(Appointment).where(Appointment.status.in_(("requested", "needs_user_info")))
                           .order_by(Appointment.created_at.desc()).limit(500))
    hits = []
    for appt in open_rows:
        clinic = appt.clinic or {}
        addrs = {str(clinic.get("email") or "").lower(), str(((clinic.get("request") or {}).get("to")) or "").lower(),
                 str((((clinic.get("requests") or {}).get("email") or {}).get("to")) or "").lower()} - {""}
        if sender in addrs:
            hits.append(appt)
    if len(hits) == 1:
        return hits[0], "sender_review"
    return None, None


def _store_for_review(db: Session, appt: Appointment, msg: ChannelMessage) -> None:
    """A reply matched by sender only: stored + the user is asked to review it; no state change."""
    from app.services import notify as notify_svc
    from app.services.booking.common import KIND_APPOINTMENT_NEEDS_INFO, append_log, entry
    from app.services.booking.orchestrator import store_messages

    msg.classification = {"outcome": "unverified", "matched_by": "sender_review"}
    store_messages(db, appt, [msg])
    append_log(appt, entry("note", text="clinic e-mail without the reply token (matched by sender only): stored "
                                        "for the patient to review; the appointment was not changed"))
    name = (appt.clinic or {}).get("name", "the clinic")
    notify_svc.notify(db, appt.user_id, KIND_APPOINTMENT_NEEDS_INFO, f"New message from {name} — please review",
                      f"{name} sent an e-mail that the assistant could not verify automatically. Please read it in "
                      "the appointment's messages and contact the clinic if needed.",
                      payload={"appointment_id": appt.id, "review": True}, email=True, commit=False)


def handle_resend_event(db: Session, payload: dict[str, Any], *, llm: Any = None,
                        fetcher: Callable[[str], dict[str, Any]] | None = None, calendar: Any = None,
                        email: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """Process one Resend webhook event (see module docstring). Commits."""
    from app.services.booking.orchestrator import apply_outcome

    if payload.get("type") != "email.received":
        return {"status": "ignored", "reason": f"event type {payload.get('type')!r}"}
    data = payload.get("data") or {}
    email_id = str(data.get("email_id") or data.get("id") or "")
    if not email_id:
        return {"status": "ignored", "reason": "no email_id"}
    external_id = f"email-in:{email_id}"
    if db.scalar(select(AppointmentMessage.id).where(AppointmentMessage.external_id == external_id)):
        return {"status": "duplicate", "email_id": email_id}

    # match on the metadata (reply token in the recipients) BEFORE fetching / parsing any body
    sender = bare_address(str(data.get("from") or ""))
    recipients = [str(x) for key in ("to", "cc", "received_for") for x in (data.get(key) or [])]
    appt, matched_by = _match(db, recipients, sender)
    if appt is None:
        log.info("inbound e-mail unmatched", extra={"email_id": email_id, "sender": sender})
        return {"status": "unmatched", "email_id": email_id}

    full = data
    if not (data.get("text") or data.get("html")):
        try:
            full = data | (fetcher or fetch_received_email)(email_id)
        except Exception as exc:
            log.warning("could not fetch received e-mail", extra={"email_id": email_id, "error": str(exc)})
    text = (str(full.get("text") or "")[:MAX_HTML]) or html_to_text(full.get("html"))
    if matched_by != "reply_token":
        msg = ChannelMessage(direction="in", channel="email", sender=sender, recipient=", ".join(recipients)[:320],
                             subject=full.get("subject"), body=(text or "")[:20000], external_id=external_id)
        _store_for_review(db, appt, msg)
        db.commit()
        return {"status": "needs_review", "email_id": email_id, "appointment_id": appt.id, "matched_by": matched_by,
                "appointment_status": appt.status}

    from app.db.models import User

    clinic = appt.clinic or {}
    tz = clinic_timezone(clinic, tz_of(db.get(User, appt.user_id)))
    proposed = [Slot.from_any(s) for s in appt.proposed_slots or []]
    reply = strip_quoted(text)
    if llm is None:
        from app.providers.llm import get_llm_provider
        from app.services import llm_audit_hook

        llm = get_llm_provider(audit_hook=llm_audit_hook(db, appt.user_id, f"appointment:{appt.id}"))
    outcome = classify_reply(llm, reply, proposed, tz, now=now)
    msg = ChannelMessage(direction="in", channel="email", sender=sender, recipient=", ".join(recipients)[:320],
                         subject=full.get("subject"), body=(text or "")[:20000], external_id=external_id)
    result = apply_outcome(db, appt, outcome | {"matched_by": matched_by}, channel="email", message=msg,
                           calendar=calendar, email=email, now=now)
    db.commit()
    return {"status": "processed", "email_id": email_id, "appointment_id": appt.id, "matched_by": matched_by,
            "outcome": outcome["outcome"], "action": result.get("action"), "appointment_status": appt.status}
