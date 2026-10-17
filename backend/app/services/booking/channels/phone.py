"""Phone channel: AI call to the clinic with ElevenLabs Agents over a Twilio number (spec §1.6 "phone").

    ch = PhoneChannel()                        # provider: ctx.providers["phone_caller"] | ElevenLabsVoice
    ch.configured()                            # ELEVENLABS_API_KEY + _AGENT_ID + _AGENT_PHONE_NUMBER_ID
    ch.available(clinic, ctx)                  # None | "not configured" | sandbox / number reason
    ch.execute(appt, ctx)                      # starts ONE outbound call -> status "requested"
    handle_post_call(db, payload)              # POST /api/webhooks/elevenlabs/post-call
    poll_conversation(db, appt)                # fallback: GET the conversation, same handling
    verify_signature(secret, header, body)     # ElevenLabs-Signature HMAC check

Flow
----
execute(): target = sandbox.resolve_target("phone", clinic) — in sandbox mode ALWAYS
SANDBOX_CLINIC_PHONE (the clinic's real number is never dialled; a second guard refuses any other
number), in live mode the clinic's number normalised to E.164 ``+1…`` (Canada only). Dynamic
variables are built ONLY from `PatientInfo.shared_fields()` — patient name, callback phone, the
generic reason category, and the onboarding identity fields the clinic may ask for (legal first /
last name, date of birth spoken + ISO, sex, phone, e-mail spelled for speech + raw, full address and
parts, postal code spelled, family doctor, has_referral, preferred language; '' when missing; the
prompt answers them ONLY when asked) — plus the clinic name, the 3 proposed slots as spoken
clinic-local times (and their ISO values so the agent can report the booked one exactly), the
clinic-local date and the appointment id (used to match the webhook). Never health data and never
a health-card number (the agent says the patient brings the card to the visit). The report link is not read aloud; the agent may
offer to e-mail a summary and records the clinic's address (`clinic_email`). The call's
conversation id becomes `appt.external_ref` / `ChannelResult.external_ref`.

Post-call: `post_call_transcription` → appointment by external_ref (fallback: the
`appointment_id` dynamic variable, accepted only when that conversation id is in the current
clinic's phone attempts) → transcript (ElevenLabs audio tags such as "[happy]" stripped from the
agent's lines) stored as an inbound AppointmentMessage
(``external_id = phone:<conversation_id>:result``, idempotent) → data-collection outcome mapped to
`orchestrator.apply_outcome` (confirmed / counter_offer / needs_info / declined / other).
``no_answer`` / ``voicemail`` / call status ``failed`` / `call_initiation_failure` → the phone
attempt is recorded as ``failed``; when no other channel request to this clinic is still open, the
existing next-channel → `next_candidate` mechanism runs (new approval for a new clinic). A missing
or unparseable booked slot is resolved from the transcript with `inbound.classify_reply`
(`llm.extract` + the deterministic fallback); a confirmation without any usable time becomes
``needs_info`` so the user is asked instead of guessing.

ElevenLabs findings (docs checked 2026-10-17)
---------------------------------------------
- Outbound call (Twilio): ``POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call``, header
  ``xi-api-key``; body ``{agent_id, agent_phone_number_id, to_number,
  conversation_initiation_client_data: {dynamic_variables: {...}, conversation_config_override?,
  user_id?, ...}, call_recording_enabled?, telephony_call_config?: {ringing_timeout_secs (60),
  twilio_machine_detection?}}`` → ``{success, message, conversation_id, callSid}``.
  https://elevenlabs.io/docs/api-reference/twilio/outbound-call
- Dynamic variables: ``{{name}}`` placeholders in the prompt / first message, values passed as
  ``dynamic_variables``; the ``system__`` prefix is reserved (``system__call_sid``,
  ``system__called_number`` …). Defaults for testing: ``conversation_config.agent.dynamic_variables
  .dynamic_variable_placeholders``.
  https://elevenlabs.io/docs/agents-platform/customization/personalization/dynamic-variables
- Import a Twilio number: ``POST /v1/convai/phone-numbers`` ``{phone_number, label, sid, token,
  provider: "twilio"}`` → ``{phone_number_id}``; list ``GET /v1/convai/phone-numbers`` (items
  ``phone_number_id, phone_number, provider, label, assigned_agent``); assign an agent ``PATCH
  /v1/convai/phone-numbers/{id}`` ``{agent_id}``.
  https://elevenlabs.io/docs/api-reference/phone-numbers/create , …/list , …/update
- Agents: ``POST /v1/convai/agents/create`` → ``{agent_id}``; ``PATCH /v1/convai/agents/{agent_id}``;
  ``GET /v1/convai/agents?search=<name>`` → ``{agents: [{agent_id, name}], has_more, next_cursor}``.
  Config: ``conversation_config.agent.{first_message, language, prompt: {prompt, llm,
  built_in_tools: {end_call, voicemail_detection}}}``, ``conversation_config.tts.voice_id``,
  ``platform_settings.data_collection: {<id>: {type: string|boolean|integer|number, description,
  enum?}}``, ``platform_settings.evaluation.criteria: [{id, name, conversation_goal_prompt,
  type: "prompt"}]``, ``platform_settings.workspace_overrides.webhooks: {post_call_webhook_id,
  events: ["transcript", "call_initiation_failure", …]}``.
  https://elevenlabs.io/docs/api-reference/agents/create , …/update , …/list
  Voicemail system tool: ``{"type": "system", "name": "voicemail_detection", "params":
  {"system_tool_type": "voicemail_detection", "voicemail_message": ""}}`` (empty = hang up).
  https://elevenlabs.io/docs/agents-platform/customization/tools/system-tools/voicemail-detection
- Post-call webhooks are WORKSPACE webhooks: ``POST /v1/workspace/webhooks`` ``{settings:
  {auth_type: "hmac", name, webhook_url}}`` → ``{webhook_id, webhook_secret}``; list ``GET
  /v1/workspace/webhooks`` (``webhooks: [{webhook_id, name, webhook_url, is_disabled, …}]``); the
  agent opts in via ``workspace_overrides.webhooks.post_call_webhook_id``.
  https://elevenlabs.io/docs/api-reference/webhooks/create , …/list
- Payloads: ``{type: "post_call_transcription", event_timestamp, data: {agent_id, conversation_id,
  status, user_id, transcript: [{role: "agent"|"user", message, time_in_call_secs}], metadata:
  {start_time_unix_secs, call_duration_secs, termination_reason, …}, analysis:
  {data_collection_results: {<id>: {data_collection_id, value, rationale, json_schema}},
  evaluation_criteria_results, call_successful: success|failure|unknown, transcript_summary},
  conversation_initiation_client_data: {dynamic_variables, …}}}``; also ``post_call_audio`` (ignored)
  and ``call_initiation_failure`` ``{data: {agent_id, conversation_id, failure_reason:
  busy|no-answer|unknown, metadata: {type: twilio|sip, body}}}``.
  https://elevenlabs.io/docs/agents-platform/workflows/post-call-webhooks
- Signature: header ``ElevenLabs-Signature: t=<unix>,v0=<hex>``; ``v0 = hex(HMAC-SHA256(secret,
  f"{t}.{raw_body}"))`` with the webhook secret used as-is (UTF-8); several ``v0=`` values may be
  present; deliveries older than 30 minutes are rejected (the SDK's ``construct_event`` does the same).
  https://elevenlabs.io/docs/agents-platform/workflows/post-call-webhooks ,
  https://hookdeck.com/webhooks/platforms/guide-to-elevenlabs-webhooks-features-and-best-practices
- Conversation fetch (poll): ``GET /v1/convai/conversations/{conversation_id}`` → same ``data``
  shape, ``status: initiated|in-progress|processing|done|failed``.
  https://elevenlabs.io/docs/api-reference/conversations/get
- Twilio trial accounts can only call verified caller IDs and play a trial preamble — fine for the
  sandbox demo (SANDBOX_CLINIC_PHONE = your own verified phone).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Appointment, AppointmentMessage
from app.integrations import Clinic, Slot
from app.services.booking import sandbox
from app.services.booking.channels.base import BookingContext, ChannelMessage, ChannelResult
from app.services.booking.common import append_log, entry, utcnow

log = get_logger(__name__)

TOLERANCE_S = 1800
OUTCOMES = ("confirmed", "counter_offer", "needs_info", "declined", "no_answer", "voicemail", "other")
FAILED_OUTCOMES = ("no_answer", "voicemail")
FAILURE_REASONS = {"no_answer": "no answer", "voicemail": "reached voicemail (no message left)",
                   "busy": "line busy", "no-answer": "no answer", "unknown": "call could not be placed"}


class WebhookVerificationError(Exception):
    pass


# ================================================================ numbers / variables

def normalize_e164(number: str | None) -> str | None:
    """'+1 416-555-0101' / '(416) 555-0101' / '14165550101' -> '+14165550101'; None if not plausible."""
    if not number:
        return None
    s = str(number).strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("+"):
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def _is_nanp(e164: str | None) -> bool:
    return bool(e164 and re.fullmatch(r"\+1\d{10}", e164))


def dial_target(clinic: Any) -> tuple[sandbox.Target | None, str | None]:
    """(target with value normalised to E.164, None) or (None, reason). Sandbox never yields the
    clinic's real number unless SANDBOX_CLINIC_PHONE is literally that number."""
    target = sandbox.resolve_target("phone", clinic)
    if target is None:
        return None, sandbox.missing_reason("phone", clinic)
    number = normalize_e164(target.value)
    if settings.booking_target_mode != "live":
        if number is None or number != normalize_e164(settings.sandbox_clinic_phone):
            return None, "sandbox: SANDBOX_CLINIC_PHONE is not a valid E.164 number"
    elif not _is_nanp(number):
        return None, "clinic phone number is not a Canadian +1 number"
    target.value = number
    return target, None


def spoken_time(slot: Slot, tz: str) -> str:
    """'Tuesday, October 21 at 10:00 AM' in the clinic's timezone."""
    s = slot.start.astimezone(ZoneInfo(tz))
    return f"{s:%A, %B} {s.day} at {f'{s:%I:%M %p}'.lstrip('0')}"


_SEX_SPOKEN = {"male": "male", "female": "female", "other": "other", "prefer_not": "prefers not to say"}
_EMAIL_WORDS = {".": " dot ", "@": " at ", "_": " underscore ", "-": " dash ", "+": " plus "}


def spoken_date(iso: str | None) -> str:
    """'1958-01-15' -> 'January 15, 1958' ('' when missing / unparseable)."""
    try:
        d = datetime.fromisoformat(str(iso)).date() if iso else None
    except ValueError:
        return ""
    return f"{d:%B} {d.day}, {d.year}" if d else ""


def spoken_email(email: str | None) -> str:
    """'r.macleod@example.com' -> 'r dot macleod at example dot com'."""
    if not email:
        return ""
    out = "".join(_EMAIL_WORDS.get(ch, ch) for ch in str(email).strip().lower())
    return re.sub(r"\s+", " ", out).strip()


def spoken_postal(code: str | None) -> str:
    """'M5H 2N2' -> 'M 5 H, 2 N 2'."""
    c = re.sub(r"\s+", "", str(code or "")).upper()
    if len(c) != 6:
        return " ".join(c)
    return f"{' '.join(c[:3])}, {' '.join(c[3:])}"


def spoken_phone(number: str | None) -> str:
    """'+1-416-555-0142' -> '4 1 6, 5 5 5, 0 1 4 2' (NANP; other numbers digit by digit)."""
    digits = re.sub(r"\D", "", str(number or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return ", ".join(" ".join(g) for g in (digits[:3], digits[3:6], digits[6:]))
    return " ".join(digits)


def dynamic_variables(appt: Any, ctx: BookingContext) -> dict[str, str]:
    """The ONLY data the voice agent gets (spec §0 minimal disclosure): the allow-listed identity /
    contact fields from onboarding (answered only when the clinic asks — see the agent prompt), the
    generic reason category and the 3 slots. Never health data. Missing values are ''."""
    shared = ctx.patient.shared_fields()
    zone = ZoneInfo(ctx.clinic_tz)
    slots = list(ctx.slots)[:3]
    spoken = [spoken_time(s, ctx.clinic_tz) for s in slots]
    now_local = (ctx.now or utcnow()).astimezone(zone)
    sv = lambda k: str(shared.get(k) or "")  # noqa: E731
    referral = shared.get("has_referral")
    dv = {
        "appointment_id": str(appt.id),
        "clinic_name": ctx.clinic.name,
        "patient_name": shared.get("name", "the patient"),
        "callback_phone": shared.get("phone", "not provided"),
        "reason_category": shared.get("reason_category", "a general check-up"),
        "preferred_slots": "; ".join(f"option {i + 1}: {t}" for i, t in enumerate(spoken)) or "flexible",
        "clinic_timezone": ctx.clinic_tz,
        "today": f"{now_local:%A, %B} {now_local.day}, {now_local.year}",
        "reminder": "yes" if ctx.reminder else "no",
        # identity fields (answer ONLY when asked)
        "patient_first_name": sv("first_name"),
        "patient_last_name": sv("last_name"),
        "date_of_birth": spoken_date(shared.get("date_of_birth")),
        "date_of_birth_iso": sv("date_of_birth"),
        "patient_sex": _SEX_SPOKEN.get(sv("sex"), sv("sex")),
        "patient_phone": sv("phone"),
        "patient_phone_spoken": spoken_phone(shared.get("phone")),
        "patient_email": sv("email"),
        "patient_email_spoken": spoken_email(shared.get("email")),
        "home_address": sv("address"),
        "street": sv("street"),
        "city": sv("city"),
        "province": sv("province"),
        "postal_code": sv("postal_code"),
        "postal_code_spoken": spoken_postal(shared.get("postal_code")) if shared.get("postal_code") else "",
        "family_doctor_name": sv("family_doctor_name"),
        "family_doctor_clinic": sv("family_doctor_clinic"),
        "family_doctor_phone": sv("family_doctor_phone"),
        "has_referral": "" if referral is None else ("yes" if referral else "no"),
        "preferred_language": sv("preferred_language"),
    }
    for i in range(3):
        dv[f"slot_{i + 1}"] = spoken[i] if i < len(spoken) else ""
        dv[f"slot_{i + 1}_iso"] = slots[i].start.astimezone(zone).isoformat() if i < len(slots) else ""
    return dv


# ================================================================ channel

class PhoneChannel:
    name = "phone"

    def __init__(self, provider: Any = None):
        self.provider = provider

    def configured(self) -> bool:
        return bool(settings.elevenlabs_api_key and settings.elevenlabs_agent_id
                    and settings.elevenlabs_agent_phone_number_id)

    def available(self, clinic: Clinic, ctx: BookingContext) -> str | None:
        if not self.configured():
            return "not configured"
        _, reason = dial_target(clinic)
        return reason

    def _provider(self, ctx: BookingContext) -> Any:
        if ctx.providers.get("phone_caller") is not None:
            return ctx.providers["phone_caller"]
        if self.provider is not None:
            return self.provider
        from app.providers.voice import get_voice_provider

        return get_voice_provider()

    def execute(self, appt: Any, ctx: BookingContext) -> ChannelResult:
        if not self.configured():
            return ChannelResult.skipped("not configured")
        target, reason = dial_target(ctx.clinic)
        if target is None:
            return ChannelResult.skipped(reason or "no phone number")
        provider = self._provider(ctx)
        if provider is None:
            return ChannelResult.skipped("not configured")
        dv = dynamic_variables(appt, ctx)
        start = provider.start_call(target.value, dv)
        sent_at = utcnow().isoformat()
        request = {"channel": "phone", "to": target.value, "real_to": target.real, "redirected": target.redirected,
                   "conversation_id": start.conversation_id, "call_sid": start.call_sid, "provider": provider.name,
                   "accepted": bool(start.ok), "error": start.error, "sent_at": sent_at,
                   "dynamic_variables": dv, "reminder": ctx.reminder or None}
        body = (f"AI assistant call to {ctx.clinic.name} ({target.value}) on behalf of {dv['patient_name']}. "
                f"Reason: {dv['reason_category']}. Proposed times: {dv['preferred_slots']}. "
                f"Callback number: {dv['callback_phone']}.")
        if target.note:
            body = f"[{target.note}] " + body
        if not start.ok:
            msg = ChannelMessage(direction="out", channel="phone", recipient=target.value, subject="Outbound call",
                                 body=body)
            return ChannelResult.failed(f"call not started: {start.error}"[:300], messages=[msg],
                                        target=target.as_dict(), details={"request": request})
        appt.external_ref = start.conversation_id
        msg = ChannelMessage(direction="out", channel="phone", recipient=target.value, subject="Outbound call",
                             body=body, external_id=f"phone:{start.conversation_id}")
        return ChannelResult(status="requested", reason=target.note or "call started",
                             external_ref=start.conversation_id, messages=[msg], target=target.as_dict(),
                             details={"request": request})


# ================================================================ webhook signature

def verify_signature(secret: str, header: str | None, body: bytes, now: float | None = None,
                     tolerance: int = TOLERANCE_S) -> None:
    """`ElevenLabs-Signature: t=<ts>,v0=<hex hmac-sha256(secret, f"{ts}.{body}")>`; raises on mismatch."""
    if not header:
        raise WebhookVerificationError("missing ElevenLabs-Signature header")
    ts, sigs = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            ts = value
        elif key == "v0":
            sigs.append(value)
    if not ts or not sigs:
        raise WebhookVerificationError("malformed signature header")
    try:
        stamp = int(ts)
    except ValueError as exc:
        raise WebhookVerificationError("bad timestamp") from exc
    if abs((now if now is not None else time.time()) - stamp) > tolerance:
        raise WebhookVerificationError("timestamp outside tolerance")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise WebhookVerificationError("no matching signature")


def sign(secret: str, body: bytes, ts: int | None = None) -> str:
    """Test/dev helper: the `ElevenLabs-Signature` header value for `body`."""
    ts = int(ts if ts is not None else time.time())
    return f"t={ts},v0=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


# ================================================================ post-call handling

def _results(data: dict[str, Any]) -> dict[str, Any]:
    """{id: value} from analysis.data_collection_results (dict) or data_collection_results_list."""
    analysis = data.get("analysis") or {}
    raw = analysis.get("data_collection_results") or {}
    items = raw.values() if isinstance(raw, dict) else raw
    items = list(items) or list(analysis.get("data_collection_results_list") or [])
    out: dict[str, Any] = {}
    for it in items:
        if isinstance(it, dict) and it.get("data_collection_id"):
            out[str(it["data_collection_id"])] = it.get("value")
    return out


#: ElevenLabs v3 audio / emotion tags in agent turns: "[happy]", "[slow]", "[laughs softly]" (1–3 words).
AUDIO_TAG_RE = re.compile(r"\[\s*[A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*){0,2}\s*\]")


def strip_audio_tags(text: str) -> str:
    """Remove square-bracket audio tags and tidy the spacing left behind."""
    out = AUDIO_TAG_RE.sub(" ", text or "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"\s+([,.!?;:])", r"\1", out).strip()


def transcript_text(data: dict[str, Any]) -> str:
    """'Assistant: …' / 'Clinic: …' lines; audio tags are stripped from the agent's turns."""
    lines = []
    for t in data.get("transcript") or []:
        msg = (t.get("message") or "").strip() if isinstance(t, dict) else ""
        if msg and t.get("role") == "agent":
            msg = strip_audio_tags(msg)
        if msg:
            lines.append(f"{'Assistant' if t.get('role') == 'agent' else 'Clinic'}: {msg}")
    return "\n".join(lines)


def _clinic_spoke(data: dict[str, Any]) -> bool:
    return any(isinstance(t, dict) and t.get("role") == "user" and (t.get("message") or "").strip()
               for t in data.get("transcript") or [])


def _phone_attempt_refs(appt: Appointment) -> set[str]:
    cid = (appt.clinic or {}).get("id")
    return {str(a.get("external_ref")) for a in appt.channel_attempts or []
            if a.get("channel") == "phone" and a.get("clinic_id") == cid and a.get("external_ref")}


def find_appointment(db: Session, data: dict[str, Any]) -> tuple[Appointment | None, str | None]:
    conv = str(data.get("conversation_id") or "")
    if conv:
        appt = db.scalar(select(Appointment).where(Appointment.external_ref == conv))
        if appt is not None:
            return appt, "external_ref"
    dv = ((data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {})
    appt_id = dv.get("appointment_id")
    if appt_id and conv:
        appt = db.get(Appointment, str(appt_id))
        if appt is not None and conv in _phone_attempt_refs(appt):
            return appt, "appointment_id"
    return None, None


def _slot_from_value(value: Any, proposed: list[Slot], tz: str) -> Slot | None:
    v = str(value or "").strip()
    if not v:
        return None
    m = re.fullmatch(r"(?:option|slot)?\s*#?([1-3])", v, re.IGNORECASE)
    if m:
        i = int(m.group(1)) - 1
        return proposed[i] if i < len(proposed) else None
    try:
        start = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo(tz))
    for s in proposed:
        if s.start == start:
            return s
    return Slot(start, start + timedelta(minutes=30))


def interpret(data: dict[str, Any], proposed: list[Slot], tz: str, llm: Any = None,
              now: datetime | None = None) -> dict[str, Any]:
    """Outcome dict for apply_outcome: {outcome, slot, requested_info, summary, method, …}."""
    from app.services.booking.inbound import classify_reply

    res = _results(data)
    text = transcript_text(data)
    summary = str(res.get("summary") or (data.get("analysis") or {}).get("transcript_summary") or "")[:300]
    kind = re.sub(r"[\s-]+", "_", str(res.get("outcome") or "").strip().lower())
    extra = {k: res[k] for k in ("confirmation_number", "referral_required", "clinic_email") if res.get(k) not in
             (None, "")}
    if data.get("status") == "failed" and kind not in OUTCOMES[:4]:
        kind = "no_answer" if not _clinic_spoke(data) else (kind or "other")
    if kind not in OUTCOMES:
        if not _clinic_spoke(data):
            kind = "no_answer"
        else:
            c = classify_reply(llm, text, proposed, tz, now=now)
            return c | {"method": f"transcript_{c.get('method')}"} | extra
    out = {"outcome": kind, "slot": "", "requested_info": str(res.get("requested_info") or "")[:300],
           "summary": summary, "method": "data_collection"} | extra
    if kind in ("confirmed", "counter_offer"):
        slot = _slot_from_value(res.get("booked_slot"), proposed, tz)
        if slot is None and text:  # ambiguous: read the time from the transcript (llm.extract + fallback)
            c = classify_reply(llm, text, proposed, tz, now=now)
            slot = _slot_from_value(c.get("slot"), proposed, tz) if c.get("outcome") in ("confirmed",
                                                                                         "counter_offer") else None
            out["method"] = f"data_collection+transcript_{c.get('method')}"
        if slot is None:
            out.update(outcome="needs_info", requested_info=(
                f"The clinic {'confirmed' if kind == 'confirmed' else 'offered another time'} by phone, but the "
                f"time was unclear. {summary}".strip()[:300]))
        else:
            out["slot"] = slot.start.isoformat()
    return out


def _resolve_llm(db: Session, appt: Appointment, llm: Any) -> Any:
    if llm is not None:
        return llm
    from app.providers.llm import get_llm_provider
    from app.services import llm_audit_hook

    return get_llm_provider(audit_hook=llm_audit_hook(db, appt.user_id, f"appointment:{appt.id}"))


def phone_failed(db: Session, appt: Appointment, conversation_id: str | None, reason: str, *,
                 calendar: Any = None, email: Any = None, now: datetime | None = None,
                 channels: dict[str, Any] | None = None) -> dict[str, Any]:
    """The call did not reach anyone (no answer / voicemail / busy). Recorded as a failed phone
    attempt; when phone was the open request for this clinic and no other channel request is still
    pending, the next channel (none after phone today) and then `next_candidate` run."""
    from app.db.models import User
    from app.services.booking.channels.base import CHANNEL_ORDER
    from app.services.booking.orchestrator import _env, _record_attempt, execute_plan, next_candidate

    cid = (appt.clinic or {}).get("id")
    current_call = appt.status == "requested" and appt.channel == "phone" and (
        not conversation_id or appt.external_ref == conversation_id)
    _record_attempt(appt, cid, "phone", "failed", reason, conversation_id)
    append_log(appt, entry("channel", channel="phone", status="failed", reason=reason, external_ref=conversation_id))
    if not current_call:
        return {"action": "recorded", "reason": reason}
    pending = [a for a in appt.channel_attempts or [] if a.get("clinic_id") == cid and a.get("channel") != "phone"
               and a.get("status") == "requested"]
    if pending:  # e.g. the e-mail request is still open: keep waiting on it
        appt.channel, appt.external_ref = pending[-1]["channel"], pending[-1].get("external_ref")
        append_log(appt, entry("note", text=f"phone: {reason}; still waiting on {appt.channel}"))
        return {"action": "waiting", "channel": appt.channel}
    user = db.get(User, appt.user_id)
    env = _env(db, user, calendar=calendar, email=email, channels=channels, now=now)
    later = [c for c in CHANNEL_ORDER if CHANNEL_ORDER.index(c) > CHANNEL_ORDER.index("phone")]
    if later and execute_plan(env, appt, only=later) is not None:
        return {"action": "next_channel", "channel": appt.channel}
    next_candidate(env, appt, f"phone: {reason}")
    return {"action": "next_candidate", "status": appt.status}


def handle_post_call(db: Session, payload: dict[str, Any], *, llm: Any = None, calendar: Any = None,
                     email: Any = None, now: datetime | None = None,
                     channels: dict[str, Any] | None = None) -> dict[str, Any]:
    """Process one ElevenLabs webhook event (see module docstring). Commits."""
    from app.services.booking.orchestrator import apply_outcome, store_messages

    kind = payload.get("type")
    data = payload.get("data") or {}
    conv = str(data.get("conversation_id") or "")
    if kind not in ("post_call_transcription", "call_initiation_failure"):
        return {"status": "ignored", "reason": f"event type {kind!r}"}
    if not conv:
        return {"status": "ignored", "reason": "no conversation_id"}
    external_id = f"phone:{conv}:result"
    if db.scalar(select(AppointmentMessage.id).where(AppointmentMessage.external_id == external_id)):
        return {"status": "duplicate", "conversation_id": conv}
    appt, matched_by = find_appointment(db, data)
    if appt is None:
        log.info("post-call webhook unmatched", extra={"conversation_id": conv})
        return {"status": "unmatched", "conversation_id": conv}
    request = ((appt.clinic or {}).get("requests") or {}).get("phone") or {}
    base = {"status": "processed", "conversation_id": conv, "appointment_id": appt.id, "matched_by": matched_by}

    if kind == "call_initiation_failure":
        why = str(data.get("failure_reason") or "unknown")
        reason = FAILURE_REASONS.get(why, f"call failed: {why}")
        store_messages(db, appt, [ChannelMessage(direction="in", channel="phone", sender=request.get("to"),
                                                 subject="Call not connected", body=f"Call not connected: {reason}",
                                                 external_id=external_id, classification={"outcome": "no_answer",
                                                                                          "failure_reason": why})])
        result = phone_failed(db, appt, conv, reason, calendar=calendar, email=email, now=now, channels=channels)
        db.commit()
        return base | {"outcome": "no_answer", "action": result["action"], "appointment_status": appt.status}

    clinic = appt.clinic or {}
    from app.db.models import User
    from app.services.booking.common import tz_of
    from app.services.booking.slots import clinic_timezone

    tz = clinic_timezone(clinic, tz_of(db.get(User, appt.user_id)))
    proposed = [Slot.from_any(s) for s in appt.proposed_slots or []]
    res = _results(data)
    needs_llm = _clinic_spoke(data) and (str(res.get("outcome") or "").strip().lower() not in OUTCOMES or (
        res.get("outcome") in ("confirmed", "counter_offer") and not res.get("booked_slot")))
    outcome = interpret(data, proposed, tz, llm=_resolve_llm(db, appt, llm) if needs_llm else llm, now=now)
    outcome["conversation_id"] = conv
    text = transcript_text(data) or "(no transcript)"
    msg = ChannelMessage(direction="in", channel="phone", sender=request.get("to"), subject="Call transcript",
                         body=text[:20000], external_id=external_id)
    if outcome["outcome"] in FAILED_OUTCOMES:
        msg.classification = outcome
        store_messages(db, appt, [msg])
        result = phone_failed(db, appt, conv, FAILURE_REASONS[outcome["outcome"]], calendar=calendar, email=email,
                              now=now, channels=channels)
    else:
        if outcome.get("confirmation_number") and outcome["outcome"] == "confirmed":
            append_log(appt, entry("note", text=f"clinic confirmation number: {outcome['confirmation_number']}"[:200]))
        if outcome.get("clinic_email"):
            append_log(appt, entry("note", text=f"clinic asked for the summary at {outcome['clinic_email']}"[:300]))
        result = apply_outcome(db, appt, outcome, channel="phone", message=msg, calendar=calendar, email=email,
                               now=now)
    db.commit()
    return base | {"outcome": outcome["outcome"], "action": result.get("action"), "appointment_status": appt.status}


def poll_conversation(db: Session, appt: Appointment, provider: Any = None, **kw: Any) -> dict[str, Any]:
    """Fallback when the webhook never arrived: fetch the conversation and handle it like the
    webhook. {status: pending|…} while the call is still running."""
    if not appt.external_ref or appt.channel != "phone":
        return {"status": "ignored", "reason": "no open phone call"}
    if provider is None:
        from app.providers.voice import get_voice_provider

        provider = get_voice_provider()
    if provider is None:
        return {"status": "ignored", "reason": "not configured"}
    conv = provider.get_conversation(appt.external_ref)
    if conv.get("status") not in ("done", "failed"):
        return {"status": "pending", "call_status": conv.get("status")}
    return handle_post_call(db, {"type": "post_call_transcription", "data": conv | {
        "conversation_id": conv.get("conversation_id") or appt.external_ref}}, **kw)
