"""Outbound AI phone calls for the booking agent (booking-v2 spec §1.6 "phone").

    provider = get_voice_provider()                  # ElevenLabsVoice when configured, else None
    start = provider.start_call("+14165550123", {"patient_name": "…", …})
    start.ok, start.conversation_id, start.call_sid, start.error
    conv = provider.get_conversation(start.conversation_id)     # fallback / poll

ElevenLabsVoice talks to the ElevenLabs Agents (Conversational AI) API with httpx:
    POST /v1/convai/twilio/outbound-call
         {agent_id, agent_phone_number_id, to_number,
          conversation_initiation_client_data: {dynamic_variables: {...}}}
         -> {success, message, conversation_id, callSid}
    GET  /v1/convai/conversations/{conversation_id}
         -> {conversation_id, status: initiated|in-progress|processing|done|failed, transcript,
             metadata, analysis: {data_collection_results, call_successful, transcript_summary}, ...}
(Endpoint details and doc URLs: app/services/booking/channels/phone.py.)

FakeVoice (tests / demos without keys) records every call and can build the post-call webhook
payload ElevenLabs would send (`post_call_payload`), so the whole chain runs offline.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import settings

API_BASE = "https://api.elevenlabs.io"
OUTBOUND_CALL_PATH = "/v1/convai/twilio/outbound-call"
CONVERSATION_PATH = "/v1/convai/conversations/{conversation_id}"

__all__ = ["API_BASE", "CallStart", "ElevenLabsVoice", "FakeVoice", "VoiceCallProvider", "get_voice_provider"]


@dataclass
class CallStart:
    ok: bool
    conversation_id: str | None = None
    call_sid: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class VoiceCallProvider(Protocol):
    name: str

    def start_call(self, to_number: str, dynamic_variables: dict[str, Any]) -> CallStart: ...

    def get_conversation(self, conversation_id: str) -> dict[str, Any]: ...


class ElevenLabsVoice:
    """ElevenLabs Agents outbound calls through an imported Twilio number."""

    name = "elevenlabs"

    def __init__(self, api_key: str, agent_id: str, phone_number_id: str, *, base_url: str = API_BASE,
                 timeout: float = 20.0, transport: httpx.BaseTransport | None = None):
        self.api_key, self.agent_id, self.phone_number_id = api_key, agent_id, phone_number_id
        self.base_url, self.timeout, self.transport = base_url.rstrip("/"), timeout, transport

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout, transport=self.transport,
                            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"})

    def start_call(self, to_number: str, dynamic_variables: dict[str, Any]) -> CallStart:
        body = {"agent_id": self.agent_id, "agent_phone_number_id": self.phone_number_id, "to_number": to_number,
                "conversation_initiation_client_data": {"dynamic_variables": dynamic_variables}}
        try:
            with self._client() as c:
                resp = c.post(OUTBOUND_CALL_PATH, json=body)
        except httpx.HTTPError as exc:
            return CallStart(False, error=f"{type(exc).__name__}: {exc}"[:300])
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            return CallStart(False, error=f"HTTP {resp.status_code}: {detail or resp.text[:200]}"[:300],
                             raw=data if isinstance(data, dict) else {})
        if not isinstance(data, dict) or not data.get("success", False) or not data.get("conversation_id"):
            msg = data.get("message") if isinstance(data, dict) else None
            return CallStart(False, error=f"call not started: {msg or 'no conversation id'}"[:300],
                             raw=data if isinstance(data, dict) else {})
        return CallStart(True, conversation_id=str(data["conversation_id"]),
                         call_sid=data.get("callSid") or data.get("call_sid"), raw=data)

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._client() as c:
            resp = c.get(CONVERSATION_PATH.format(conversation_id=conversation_id))
        resp.raise_for_status()
        return resp.json()


class FakeVoice:
    """Records calls; `post_call_payload` builds the matching ElevenLabs webhook body."""

    name = "fake"
    _ids = itertools.count(1)

    def __init__(self, fail: str | None = None):
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.conversations: dict[str, dict[str, Any]] = {}

    def start_call(self, to_number: str, dynamic_variables: dict[str, Any]) -> CallStart:
        self.calls.append({"to_number": to_number, "dynamic_variables": dict(dynamic_variables)})
        if self.fail:
            return CallStart(False, error=self.fail)
        n = next(self._ids)
        conv = f"conv_fake_{n:04d}"
        self.conversations[conv] = {"conversation_id": conv, "status": "in-progress", "to_number": to_number,
                                    "dynamic_variables": dict(dynamic_variables)}
        return CallStart(True, conversation_id=conv, call_sid=f"CA{n:032d}")

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        conv = self.conversations.get(conversation_id)
        if conv is None:
            raise KeyError(conversation_id)
        return conv.get("final") or {"conversation_id": conversation_id, "status": conv["status"]}

    def post_call_payload(self, conversation_id: str, outcome: str | None = "confirmed", *,
                          booked_slot: str = "", requested_info: str = "", summary: str = "",
                          transcript: list[tuple[str, str]] | None = None, status: str = "done",
                          extra_results: dict[str, Any] | None = None, termination_reason: str = "",
                          ) -> dict[str, Any]:
        """A `post_call_transcription` webhook body as ElevenLabs sends it (also stored for
        `get_conversation`, so polling sees the same data)."""
        conv = self.conversations.get(conversation_id, {})
        values: dict[str, Any] = {"outcome": outcome, "booked_slot": booked_slot, "requested_info": requested_info,
                                  "summary": summary} | (extra_results or {})
        results = {k: {"data_collection_id": k, "value": v, "rationale": "fake", "json_schema": None}
                   for k, v in values.items() if v is not None}
        turns = transcript or [("agent", "Hello, I'm an AI assistant calling on behalf of a patient."),
                               ("user", summary or "Okay.")]
        data = {
            "agent_id": settings.elevenlabs_agent_id or "agent_fake", "conversation_id": conversation_id,
            "status": status, "user_id": None,
            "transcript": [{"role": r, "message": m, "time_in_call_secs": i * 5} for i, (r, m) in enumerate(turns)],
            "metadata": {"start_time_unix_secs": int(time.time()) - 120, "call_duration_secs": 95,
                         "termination_reason": termination_reason},
            "analysis": {"evaluation_criteria_results": {}, "data_collection_results": results,
                         "call_successful": "success" if outcome == "confirmed" else "unknown",
                         "transcript_summary": summary},
            "conversation_initiation_client_data": {"dynamic_variables": conv.get("dynamic_variables", {})},
        }
        if conversation_id in self.conversations:
            self.conversations[conversation_id]["final"] = data
            self.conversations[conversation_id]["status"] = status
        return {"type": "post_call_transcription", "event_timestamp": int(time.time()), "data": data}

    def failure_payload(self, conversation_id: str, failure_reason: str = "no-answer") -> dict[str, Any]:
        """A `call_initiation_failure` webhook body (busy / no-answer / unknown)."""
        return {"type": "call_initiation_failure", "event_timestamp": int(time.time()),
                "data": {"agent_id": settings.elevenlabs_agent_id or "agent_fake", "conversation_id": conversation_id,
                         "failure_reason": failure_reason, "metadata": {"type": "twilio", "body": {}}}}


def get_voice_provider() -> VoiceCallProvider | None:
    """ElevenLabsVoice when ELEVENLABS_API_KEY / _AGENT_ID / _AGENT_PHONE_NUMBER_ID are set, else None."""
    if settings.elevenlabs_api_key and settings.elevenlabs_agent_id and settings.elevenlabs_agent_phone_number_id:
        return ElevenLabsVoice(settings.elevenlabs_api_key, settings.elevenlabs_agent_id,
                               settings.elevenlabs_agent_phone_number_id)
    return None
