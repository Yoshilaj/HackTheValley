"""Shared LLM abstraction (chat, document extraction, report writing, booking agent).

Interface (`LlmProvider`):
    complete(system, messages, max_tokens=1024, temperature=None, *, model=None,
             purpose="complete", audit_hook=None) -> LlmResponse
    extract(system, user_content, schema, model=None, *, tool_name="record",
            purpose="extract", audit_hook=None) -> dict
    run_tools(system, messages, tools: list[ToolSpec], max_turns=8, *, model=None,
              max_tokens=4096, purpose="run_tools", audit_hook=None) -> (final_text, transcript)
    vision_extract(image_bytes, media_type, system, schema, *, model=None,
                   purpose="vision_extract", audit_hook=None) -> dict
    transcribe_document(data: bytes | str) -> str        # pypdf text for PDFs

Implementations: `GeminiLlm` (real, `google-genai` SDK, lazy import), `AnthropicLlm` (real,
`anthropic` SDK, lazy import) and `FakeLlm` (deterministic, offline). `get_llm_provider()` picks
one from `settings.llm_provider`.

Auditing: the provider never touches the DB. Every call invokes `audit_hook(detail)` (per-call
argument, else the constructor's) with `{model, input_tokens, output_tokens,
cache_read_input_tokens, purpose, stop_reason}`. `app.services.llm_audit_hook(db, user_id)`
builds a hook that writes an `AuditLog(action='llm_call')` row.

Anthropic API notes (claude-api skill, 2026-10):
- Default model `claude-sonnet-5-5`, cheap model `claude-haiku-5-5`.
- Forced `tool_choice` (`any`/`tool`) returns 400 on Sonnet 5.5, so `extract` offers exactly one
  tool with `tool_choice=auto` plus an instruction to call it, re-prompts once if no call
  comes back, and finally falls back to parsing JSON text.
- Non-default sampling params are rejected on the 5.x models, so `temperature` is only sent to
  older models.
- The system prompt is sent as a text block with `cache_control: ephemeral` (prompt caching;
  short prompts below the model's minimum silently don't cache, which is harmless).
- On Sonnet 5.5 / Opus 5.5 the server-side refusal fallback (`fallbacks="default"`, beta
  `server-side-fallback-2026-07-01`) is enabled; `stop_reason == "refusal"` is checked.

Gemini notes (`GeminiLlm`):
- Default `settings.gemini_model`, cheap `settings.gemini_cheap_model`. Callers pass
  `llm.model_cheap` / `llm.model_default` or Claude model names; Claude names are translated
  (haiku / `CHEAP_MODEL` → cheap, any other `claude-*` → default); `gemini-*` names pass through.
- Messages are Anthropic-style (`role` user/assistant, str or content blocks); they are converted
  to `types.Content` (assistant → model; text/image/document/tool_use/tool_result → parts).
- `extract`/`vision_extract` use structured output (`response_mime_type=application/json` +
  `response_json_schema`, our JSON Schema lightly sanitised). If the API rejects the schema the
  call is retried without it (schema embedded in the prompt), one re-prompt on unparsable output,
  then a JSON-text fallback.
- `run_tools` disables the SDK's automatic function calling and runs the loop itself; the raw
  model `Content` is appended unchanged so Gemini 3 thought signatures survive between turns.
- Stop reasons are mapped to the Anthropic vocabulary (`end_turn`, `tool_use`, `max_tokens`,
  `refusal`) so audit rows look the same across providers. `temperature` is not sent to
  Gemini 3 models (Google recommends the default 1.0 there).
- Usage: `prompt_token_count` → input, `candidates_token_count + thoughts_token_count` → output,
  `cached_content_token_count` → cache_read.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

AuditHook = Callable[[dict[str, Any]], None]

DEFAULT_MODEL = "claude-sonnet-5-5"
CHEAP_MODEL = "claude-haiku-5-5"
#: Models that reject non-default temperature/top_p/top_k.
_NO_SAMPLING_PREFIXES = ("claude-sonnet-5", "claude-haiku-5", "claude-opus-5", "claude-fable",
                         "claude-mythos", "claude-opus-4-7", "claude-opus-4-8")
#: Models that accept the server-side refusal fallback "default" mode on the Claude API.
_FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
VISION_MEDIA_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


class LlmError(RuntimeError):
    """The model did not produce a usable answer (refusal, no tool call, bad JSON)."""


class LlmConfigError(LlmError):
    """The provider is not configured (e.g. ANTHROPIC_API_KEY missing)."""


class LlmApiError(LlmError):
    """The provider API returned an error (quota / 429, 5xx, bad request). ``code`` = HTTP status."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class LlmUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class LlmResponse:
    text: str
    model: str
    usage: LlmUsage = field(default_factory=LlmUsage)
    stop_reason: str | None = None


@dataclass
class ToolSpec:
    """A client tool for `run_tools`. `handler(input_dict)` returns any JSON-serialisable
    value (or str); raising marks the tool_result `is_error`."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


class LlmProvider(Protocol):
    model_default: str
    model_cheap: str

    def complete(self, system: str, messages: list[dict[str, Any]], max_tokens: int = 1024,
                 temperature: float | None = None, *, model: str | None = None,
                 purpose: str = "complete", audit_hook: AuditHook | None = None) -> LlmResponse: ...

    def stream(self, system: str, messages: list[dict[str, Any]], max_tokens: int = 1024,
               temperature: float | None = None, *, model: str | None = None,
               purpose: str = "stream", audit_hook: AuditHook | None = None) -> Iterator[tuple[str, str]]: ...

    def extract(self, system: str, user_content: str | list[dict[str, Any]], schema: dict[str, Any],
                model: str | None = None, *, tool_name: str = "record", purpose: str = "extract",
                audit_hook: AuditHook | None = None) -> dict[str, Any]: ...

    def run_tools(self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec],
                  max_turns: int = 8, *, model: str | None = None, max_tokens: int = 4096,
                  purpose: str = "run_tools",
                  audit_hook: AuditHook | None = None) -> tuple[str, list[dict[str, Any]]]: ...

    def vision_extract(self, image_bytes: bytes, media_type: str, system: str, schema: dict[str, Any],
                       *, model: str | None = None, purpose: str = "vision_extract",
                       audit_hook: AuditHook | None = None) -> dict[str, Any]: ...

    def transcribe_document(self, data: bytes | str) -> str: ...


# ---------------------------------------------------------------- shared helpers

def pdf_to_text(pdf_bytes: bytes) -> str:
    """Extract text from a PDF with pypdf (no OCR; scanned PDFs return '')."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def transcribe_document(data: bytes | str) -> str:
    """PDF bytes → pypdf text; other bytes → UTF-8 text; str → unchanged."""
    if isinstance(data, str):
        return data
    if data[:5] == b"%PDF-":
        return pdf_to_text(data)
    return data.decode("utf-8", errors="replace")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _extract_instruction(tool_name: str) -> str:
    return (f"\n\nRecord your answer by calling the `{tool_name}` tool exactly once. "
            "Use null (or an empty list) for anything the input does not state; never guess.")


def _parse_json_text(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


class _Base:
    model_default: str
    model_cheap: str

    def __init__(self, audit_hook: AuditHook | None = None):
        self.audit_hook = audit_hook

    def _audit(self, hook: AuditHook | None, model: str, usage: LlmUsage, purpose: str,
               stop_reason: str | None = None) -> None:
        hook = hook or self.audit_hook
        if hook is None:
            return
        try:
            hook({
                "model": model, "purpose": purpose, "stop_reason": stop_reason,
                "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                "cache_read_input_tokens": usage.cache_read_input_tokens,
            })
        except Exception:  # auditing must never break the call
            log.exception("llm audit hook failed")

    def stream(self, system, messages, max_tokens=1024, temperature=None, *, model=None,
               purpose="stream", audit_hook=None) -> Iterator[tuple[str, str]]:
        """Yield ("thought", summary) / ("text", chunk) as the answer is written. Default: the whole
        `complete` answer as one chunk; providers that can stream override it."""
        resp = self.complete(system, messages, max_tokens, temperature, model=model, purpose=purpose,
                             audit_hook=audit_hook)
        if resp.text:
            yield "text", resp.text

    def transcribe_document(self, data: bytes | str) -> str:
        return transcribe_document(data)


# ================================================================ Anthropic

class AnthropicLlm(_Base):
    """Real provider using the `anthropic` SDK (beta Messages endpoint for the refusal fallback)."""

    def __init__(self, model_default: str = DEFAULT_MODEL, model_cheap: str = CHEAP_MODEL,
                 api_key: str | None = None, audit_hook: AuditHook | None = None,
                 use_fallbacks: bool = True, timeout: float = 120.0):
        super().__init__(audit_hook)
        self.model_default = model_default
        self.model_cheap = model_cheap
        self._api_key = api_key
        self.use_fallbacks = use_fallbacks
        self.timeout = timeout
        self._client = None

    @property
    def client(self):
        if self._client is None:
            key = self._api_key or settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                raise LlmConfigError(
                    "ANTHROPIC_API_KEY is not set. Set it in the environment/.env, "
                    "or use LLM_PROVIDER=fake for offline development."
                )
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise LlmConfigError("the 'anthropic' package is not installed") from e
            self._client = anthropic.Anthropic(api_key=key, timeout=self.timeout)
        return self._client

    # -- low level
    def _create(self, *, model: str, system: str, messages: list[dict[str, Any]], max_tokens: int,
                temperature: float | None = None, tools: list[dict[str, Any]] | None = None,
                purpose: str, audit_hook: AuditHook | None):
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools
        if temperature is not None and not model.startswith(_NO_SAMPLING_PREFIXES):
            kwargs["temperature"] = temperature
        if self.use_fallbacks and model in _FALLBACK_MODELS:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        resp = self.client.beta.messages.create(**kwargs)
        u = resp.usage
        usage = LlmUsage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        self._audit(audit_hook, resp.model, usage, purpose, resp.stop_reason)
        return resp, usage

    @staticmethod
    def _text(resp) -> str:
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")

    # -- interface
    def complete(self, system, messages, max_tokens=1024, temperature=None, *, model=None,
                 purpose="complete", audit_hook=None) -> LlmResponse:
        model = model or self.model_default
        resp, usage = self._create(model=model, system=system, messages=messages, max_tokens=max_tokens,
                                   temperature=temperature, purpose=purpose, audit_hook=audit_hook)
        return LlmResponse(text=self._text(resp), model=resp.model, usage=usage, stop_reason=resp.stop_reason)

    def _extract_with(self, system: str, content: str | list[dict[str, Any]], schema: dict[str, Any],
                      model: str, tool_name: str, purpose: str, audit_hook: AuditHook | None) -> dict[str, Any]:
        tool = {"name": tool_name, "description": "Record the extracted fields.", "input_schema": schema}
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        sys = system + _extract_instruction(tool_name)
        for attempt in range(2):
            resp, _ = self._create(model=model, system=sys, messages=messages, max_tokens=4096,
                                   tools=[tool], purpose=purpose, audit_hook=audit_hook)
            if resp.stop_reason == "refusal":
                raise LlmError("model declined the extraction request")
            for b in resp.content:
                if getattr(b, "type", None) == "tool_use" and b.name == tool_name:
                    data = b.input if isinstance(b.input, dict) else json.loads(b.input)
                    return dict(data)
            if attempt == 0:  # re-prompt once (auto tool_choice does not guarantee a call)
                messages = messages + [
                    {"role": "assistant", "content": resp.content},
                    {"role": "user", "content": f"Please call the `{tool_name}` tool now."},
                ]
        parsed = _parse_json_text(self._text(resp))
        if parsed is None:
            raise LlmError("model did not return the extraction tool call")
        return parsed

    def extract(self, system, user_content, schema, model=None, *, tool_name="record",
                purpose="extract", audit_hook=None) -> dict[str, Any]:
        return self._extract_with(system, user_content, schema, model or self.model_cheap,
                                  tool_name, purpose, audit_hook)

    def vision_extract(self, image_bytes, media_type, system, schema, *, model=None,
                       purpose="vision_extract", audit_hook=None) -> dict[str, Any]:
        data = base64.standard_b64encode(image_bytes).decode("ascii")
        if media_type == "application/pdf":
            block = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": data}}
        elif media_type in VISION_MEDIA_TYPES:
            block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
        else:
            raise LlmError(f"unsupported media type for vision: {media_type}")
        content = [block, {"type": "text", "text": "Extract the fields from this file."}]
        return self._extract_with(system, content, schema, model or self.model_default,
                                  "record", purpose, audit_hook)

    def run_tools(self, system, messages, tools, max_turns=8, *, model=None, max_tokens=4096,
                  purpose="run_tools", audit_hook=None) -> tuple[str, list[dict[str, Any]]]:
        """Manual agentic loop (claude-api skill 'Manual Agentic Loop'): call → run every
        tool_use block → return all tool_results in one user message → repeat."""
        model = model or self.model_default
        specs = {t.name: t for t in tools}
        tool_defs = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        convo = list(messages)
        transcript: list[dict[str, Any]] = []
        final_text = ""
        for turn in range(max_turns):
            resp, _ = self._create(model=model, system=system, messages=convo, max_tokens=max_tokens,
                                   tools=tool_defs, purpose=purpose, audit_hook=audit_hook)
            text = self._text(resp)
            if text:
                final_text = text
                transcript.append({"ts": _now_iso(), "turn": turn, "type": "assistant_text", "text": text})
            if resp.stop_reason == "refusal":
                transcript.append({"ts": _now_iso(), "turn": turn, "type": "refusal"})
                break
            if resp.stop_reason == "pause_turn":
                convo.append({"role": "assistant", "content": resp.content})
                continue
            uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if resp.stop_reason != "tool_use" or not uses:
                break
            convo.append({"role": "assistant", "content": resp.content})
            results = []
            for b in uses:
                entry, result = _run_handler(specs, b.name, dict(b.input or {}), turn)
                transcript.append(entry)
                results.append(result | {"tool_use_id": b.id})
            convo.append({"role": "user", "content": results})
        else:
            transcript.append({"ts": _now_iso(), "type": "max_turns_reached", "max_turns": max_turns})
        return final_text, transcript


def _run_handler(specs: dict[str, ToolSpec], name: str, tool_input: dict[str, Any],
                 turn: int) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = specs.get(name)
    try:
        if spec is None:
            raise KeyError(f"unknown tool {name!r}")
        output = spec.handler(tool_input)
        is_error = False
    except Exception as e:  # tool errors go back to the model as is_error results
        output = f"{type(e).__name__}: {e}"
        is_error = True
    entry = {"ts": _now_iso(), "turn": turn, "type": "tool", "tool": name, "input": tool_input,
             "output": output, "is_error": is_error}
    result: dict[str, Any] = {"type": "tool_result", "content": _jsonable(output)}
    if is_error:
        result["is_error"] = True
    return entry, result


# ================================================================ Gemini

GEMINI_VISION_MEDIA_TYPES = (*VISION_MEDIA_TYPES, "image/heic", "image/heif", "application/pdf")
_GEMINI_REFUSAL_REASONS = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY",
                           "IMAGE_PROHIBITED_CONTENT", "RECITATION"}
_SCHEMA_DROP_KEYS = {"$schema", "$id", "default", "examples", "$comment"}


def gemini_schema(schema: Any) -> Any:
    """Make one of our JSON Schemas acceptable to Gemini's `response_json_schema` /
    `parameters_json_schema`: drop annotation keys Gemini rejects and move `None` out of `enum`
    (Gemini enums are strings only) into a nullable `type`."""
    if isinstance(schema, list):
        return [gemini_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in _SCHEMA_DROP_KEYS:
            continue
        if k in ("properties", "$defs", "definitions") and isinstance(v, dict):
            out[k] = {name: gemini_schema(sub) for name, sub in v.items()}
        elif k in ("items", "additionalProperties", "not") and isinstance(v, dict):
            out[k] = gemini_schema(v)
        elif k in ("anyOf", "oneOf", "allOf", "prefixItems") and isinstance(v, list):
            out[k] = [gemini_schema(x) for x in v]
        else:
            out[k] = v
    enum = out.get("enum")
    if isinstance(enum, list) and None in enum:
        out["enum"] = [e for e in enum if e is not None]
        typ = out.get("type")
        if isinstance(typ, str) and typ != "null":
            out["type"] = [typ, "null"]
        elif isinstance(typ, list) and "null" not in typ:
            out["type"] = [*typ, "null"]
        elif typ is None:
            out["type"] = ["string", "null"]
    return out


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _json_payload(value: Any) -> Any:
    """Round-trip through JSON so handler outputs (datetimes, dataclasses...) fit a protobuf Struct."""
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, str):
        return value
    try:
        return json.loads(_jsonable(value))
    except (TypeError, ValueError):
        return str(value)


class GeminiLlm(_Base):
    """Real provider using the `google-genai` SDK (Gemini Developer API)."""

    #: "model is experiencing high demand" 5xx responses are common and short-lived: retry once.
    #: 429 (free tier: 5 requests/min per model) is not retried; it surfaces as LlmApiError.
    TRANSIENT_RETRIES = 1
    TRANSIENT_RETRY_DELAY_S = 2.0
    #: Extra output tokens for the model's internal thinking on thinking models (see _generate).
    THINKING_ALLOWANCE = 4096

    def __init__(self, model_default: str | None = None, model_cheap: str | None = None,
                 api_key: str | None = None, audit_hook: AuditHook | None = None,
                 timeout: float = 120.0, client: Any = None):
        super().__init__(audit_hook)
        self.model_default = model_default or settings.gemini_model
        self.model_cheap = model_cheap or settings.gemini_cheap_model
        self._api_key = api_key
        self.timeout = timeout
        self._client = client

    @property
    def client(self):
        if self._client is None:
            key = self._api_key or settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
            if not key:
                raise LlmConfigError(
                    "GEMINI_API_KEY is not set. Set it in the environment/.env, "
                    "or use LLM_PROVIDER=fake for offline development."
                )
            try:
                from google import genai
                from google.genai import types
            except ImportError as e:  # pragma: no cover
                raise LlmConfigError("the 'google-genai' package is not installed") from e
            self._client = genai.Client(api_key=key,
                                        http_options=types.HttpOptions(timeout=int(self.timeout * 1000)))
        return self._client

    @staticmethod
    def _types():
        try:
            from google.genai import types
        except ImportError as e:  # pragma: no cover
            raise LlmConfigError("the 'google-genai' package is not installed") from e
        return types

    def resolve_model(self, model: str | None, default: str | None = None) -> str:
        """Translate caller model names (Claude names, CHEAP_MODEL) to Gemini models."""
        if not model:
            return default or self.model_default
        m = model.lower()
        if model == self.model_cheap or model == CHEAP_MODEL or m == "cheap" or "haiku" in m:
            return self.model_cheap
        if model == DEFAULT_MODEL or m.startswith("claude") or m in ("default", "fake-llm"):
            return self.model_default
        if m == "fake-llm-cheap":
            return self.model_cheap
        return model

    # -- conversion
    def to_contents(self, messages: list[dict[str, Any]]) -> list[Any]:
        """Anthropic-style messages → `types.Content` list. `types.Content` items pass through."""
        types = self._types()
        tool_names: dict[str, str] = {}
        contents: list[Any] = []
        built: set[int] = set()  # Content objects created here (safe to merge into)
        for msg in messages:
            if isinstance(msg, types.Content):
                contents.append(msg)
                continue
            role = "model" if _attr(msg, "role") == "assistant" else "user"
            content = _attr(msg, "content")
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content or [])
            parts = []
            for b in blocks:
                if isinstance(b, types.Part):
                    parts.append(b)
                    continue
                if isinstance(b, str):
                    parts.append(types.Part(text=b))
                    continue
                kind = _attr(b, "type")
                if kind == "text":
                    if _attr(b, "text"):
                        parts.append(types.Part(text=_attr(b, "text")))
                elif kind in ("image", "document"):
                    src = _attr(b, "source") or {}
                    if _attr(src, "type") == "base64":
                        parts.append(types.Part.from_bytes(data=base64.b64decode(_attr(src, "data")),
                                                           mime_type=_attr(src, "media_type")))
                    elif _attr(src, "type") == "text":
                        parts.append(types.Part(text=_attr(src, "data", "")))
                    elif _attr(src, "type") == "url":
                        parts.append(types.Part(file_data=types.FileData(file_uri=_attr(src, "url"))))
                elif kind == "tool_use":
                    tool_names[_attr(b, "id")] = _attr(b, "name")
                    parts.append(types.Part(function_call=types.FunctionCall(
                        id=_attr(b, "id"), name=_attr(b, "name"), args=dict(_attr(b, "input") or {}))))
                elif kind == "tool_result":
                    tid = _attr(b, "tool_use_id")
                    raw = _attr(b, "content")
                    if isinstance(raw, list):
                        raw = "".join(_attr(x, "text", "") for x in raw)
                    key = "error" if _attr(b, "is_error") else "result"
                    parts.append(types.Part(function_response=types.FunctionResponse(
                        id=tid, name=tool_names.get(tid, "tool"), response={key: raw})))
                elif kind in ("thinking", "redacted_thinking"):
                    continue
                elif _attr(b, "text"):
                    parts.append(types.Part(text=_attr(b, "text")))
            if not parts:
                continue
            if contents and contents[-1].role == role and id(contents[-1]) in built:
                contents[-1].parts.extend(parts)  # Gemini wants alternating turns
            else:
                contents.append(types.Content(role=role, parts=parts))
                built.add(id(contents[-1]))
        return contents

    # -- low level
    @staticmethod
    def _parts(resp) -> list[Any]:
        cands = getattr(resp, "candidates", None) or []
        content = getattr(cands[0], "content", None) if cands else None
        return list(getattr(content, "parts", None) or [])

    @classmethod
    def _text(cls, resp) -> str:
        return "".join(p.text for p in cls._parts(resp) if getattr(p, "text", None) and not getattr(p, "thought", False))

    @classmethod
    def _stop_reason(cls, resp) -> str:
        fb = getattr(resp, "prompt_feedback", None)
        if fb is not None and getattr(fb, "block_reason", None):
            return "refusal"
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            return "refusal"
        fr = getattr(cands[0], "finish_reason", None)
        name = str(getattr(fr, "name", None) or getattr(fr, "value", None) or fr or "STOP").upper()
        if name.startswith("FINISHREASON."):
            name = name.split(".", 1)[1]
        if name in _GEMINI_REFUSAL_REASONS:
            return "refusal"
        if name == "MAX_TOKENS":
            return "max_tokens"
        if any(getattr(p, "function_call", None) for p in cls._parts(resp)):
            return "tool_use"
        if name in ("STOP", "FINISH_REASON_UNSPECIFIED"):
            return "end_turn"
        return name.lower()

    def _config(self, *, model: str, system: str, max_tokens: int, temperature: float | None = None,
                tools: list[Any] | None = None, json_schema: dict[str, Any] | None = None,
                json_mode: bool = False, include_thoughts: bool = False):
        types = self._types()
        # Thinking models (Gemini 2.5 / 3.x) count thought tokens against max_output_tokens; without an
        # allowance a 1024-token chat answer was cut mid-sentence (stop_reason=max_tokens, live 2026-10-17).
        thinking = model.startswith(("gemini-3", "gemini-2.5"))
        cfg: dict[str, Any] = dict(system_instruction=system or None,
                                   max_output_tokens=max_tokens + (self.THINKING_ALLOWANCE if thinking else 0))
        if temperature is not None and not model.startswith("gemini-3"):
            cfg["temperature"] = temperature
        if tools:
            cfg["tools"] = [types.Tool(function_declarations=tools)]
            cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        if json_schema is not None or json_mode:
            cfg["response_mime_type"] = "application/json"
        if json_schema is not None:
            cfg["response_json_schema"] = json_schema
        if include_thoughts and thinking:  # short summaries of the thinking, streamed before the answer
            cfg["thinking_config"] = types.ThinkingConfig(include_thoughts=True)
        return types.GenerateContentConfig(**cfg)

    @staticmethod
    def _genai_errors():
        try:
            from google.genai import errors as genai_errors
        except ImportError:  # pragma: no cover
            return None
        return genai_errors

    @staticmethod
    def _usage(resp) -> LlmUsage:
        u = getattr(resp, "usage_metadata", None)
        return LlmUsage(
            input_tokens=getattr(u, "prompt_token_count", 0) or 0,
            output_tokens=(getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0),
            cache_read_input_tokens=getattr(u, "cached_content_token_count", 0) or 0,
        )

    def _generate(self, *, model: str, system: str, contents: list[Any], max_tokens: int,
                  temperature: float | None = None, tools: list[Any] | None = None,
                  json_schema: dict[str, Any] | None = None, json_mode: bool = False,
                  purpose: str, audit_hook: AuditHook | None):
        config = self._config(model=model, system=system, max_tokens=max_tokens, temperature=temperature,
                              tools=tools, json_schema=json_schema, json_mode=json_mode)
        client = self.client
        genai_errors = self._genai_errors()
        for attempt in range(self.TRANSIENT_RETRIES + 1):
            try:
                resp = client.models.generate_content(model=model, contents=contents, config=config)
                break
            except Exception as e:
                # SDK errors (429 RESOURCE_EXHAUSTED on the free tier, 5xx, 400) become LlmError so callers
                # degrade (chat apology + sources, deterministic booking fallback) instead of a 500.
                if genai_errors is None or not isinstance(e, genai_errors.APIError):
                    raise
                code = getattr(e, "code", None)
                if code in (500, 502, 503, 504) and attempt < self.TRANSIENT_RETRIES:
                    log.warning("gemini %s (%s), retrying once in %.1fs", code, purpose, self.TRANSIENT_RETRY_DELAY_S)
                    time.sleep(self.TRANSIENT_RETRY_DELAY_S)
                    continue
                raise LlmApiError(f"gemini API error {code} ({purpose}): {str(e)[:500]}", code=code) from e
        usage = self._usage(resp)
        stop = self._stop_reason(resp)
        self._audit(audit_hook, getattr(resp, "model_version", None) or model, usage, purpose, stop)
        return resp, usage, stop

    # -- interface
    def complete(self, system, messages, max_tokens=1024, temperature=None, *, model=None,
                 purpose="complete", audit_hook=None) -> LlmResponse:
        model = self.resolve_model(model)
        resp, usage, stop = self._generate(model=model, system=system, contents=self.to_contents(messages),
                                           max_tokens=max_tokens, temperature=temperature, purpose=purpose,
                                           audit_hook=audit_hook)
        return LlmResponse(text=self._text(resp), model=getattr(resp, "model_version", None) or model,
                           usage=usage, stop_reason=stop)

    def stream(self, system, messages, max_tokens=1024, temperature=None, *, model=None,
               purpose="stream", audit_hook=None) -> Iterator[tuple[str, str]]:
        """`complete`, streamed: thought summaries ("thought") while the model thinks, then the answer
        ("text") as it's written. A transient 5xx is retried only before anything was yielded."""
        model = self.resolve_model(model)
        config = self._config(model=model, system=system, max_tokens=max_tokens, temperature=temperature,
                              include_thoughts=True)
        contents = self.to_contents(messages)
        client = self.client
        genai_errors = self._genai_errors()
        last, started = None, False
        for attempt in range(self.TRANSIENT_RETRIES + 1):
            try:
                for chunk in client.models.generate_content_stream(model=model, contents=contents, config=config):
                    last = chunk
                    for p in self._parts(chunk):
                        if getattr(p, "text", None):
                            started = True
                            yield ("thought" if getattr(p, "thought", False) else "text"), p.text
                break
            except Exception as e:
                if genai_errors is None or not isinstance(e, genai_errors.APIError):
                    raise
                code = getattr(e, "code", None)
                if code in (500, 502, 503, 504) and attempt < self.TRANSIENT_RETRIES and not started:
                    log.warning("gemini %s (%s), retrying once in %.1fs", code, purpose, self.TRANSIENT_RETRY_DELAY_S)
                    time.sleep(self.TRANSIENT_RETRY_DELAY_S)
                    continue
                raise LlmApiError(f"gemini API error {code} ({purpose}): {str(e)[:500]}", code=code) from e
        self._audit(audit_hook, getattr(last, "model_version", None) or model, self._usage(last), purpose,
                    self._stop_reason(last) if last is not None else "refusal")

    @staticmethod
    def _is_schema_error(exc: Exception) -> bool:
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        msg = str(exc).lower()
        return code == 400 and any(w in msg for w in ("schema", "response_json", "invalid json payload", "enum"))

    def _extract_with(self, system: str, content: str | list[dict[str, Any]], schema: dict[str, Any],
                      model: str, purpose: str, audit_hook: AuditHook | None) -> dict[str, Any]:
        types = self._types()
        g_schema = gemini_schema(schema)
        sys = (system + "\n\nAnswer with a single JSON object matching the response schema. "
               "Use null (or an empty list) for anything the input does not state; never guess.")
        contents = self.to_contents([{"role": "user", "content": content}])
        use_schema = True
        text = ""
        for attempt in range(2):
            try:
                resp, _, stop = self._generate(
                    model=model, system=sys if use_schema else sys + "\n\nJSON Schema:\n" + json.dumps(schema),
                    contents=contents, max_tokens=8192, json_schema=g_schema if use_schema else None,
                    json_mode=True, purpose=purpose, audit_hook=audit_hook)
            except Exception as e:
                if not (use_schema and self._is_schema_error(e)):
                    raise
                log.warning("gemini rejected response schema, retrying without it: %s", str(e)[:300])
                use_schema = False
                resp, _, stop = self._generate(
                    model=model, system=sys + "\n\nJSON Schema:\n" + json.dumps(schema), contents=contents,
                    max_tokens=8192, json_mode=True, purpose=purpose, audit_hook=audit_hook)
            if stop == "refusal":
                raise LlmError("model declined the extraction request")
            text = self._text(resp)
            parsed = getattr(resp, "parsed", None)
            if isinstance(parsed, dict):
                return dict(parsed)
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
            except (TypeError, ValueError):
                pass
            if attempt == 0:  # re-prompt once
                model_content = (resp.candidates[0].content if getattr(resp, "candidates", None)
                                 and resp.candidates[0].content else types.Content(role="model", parts=[types.Part(text=text or "")]))
                contents = contents + [model_content, types.Content(role="user", parts=[types.Part(
                    text="That was not a valid JSON object. Reply with only the JSON object.")])]
        parsed = _parse_json_text(text)
        if parsed is None:
            raise LlmError("model did not return a JSON object for the extraction")
        return parsed

    def extract(self, system, user_content, schema, model=None, *, tool_name="record",
                purpose="extract", audit_hook=None) -> dict[str, Any]:
        return self._extract_with(system, user_content, schema, self.resolve_model(model, self.model_cheap),
                                  purpose, audit_hook)

    def vision_extract(self, image_bytes, media_type, system, schema, *, model=None,
                       purpose="vision_extract", audit_hook=None) -> dict[str, Any]:
        if media_type not in GEMINI_VISION_MEDIA_TYPES:
            raise LlmError(f"unsupported media type for vision: {media_type}")
        types = self._types()
        content = [types.Part.from_bytes(data=image_bytes, mime_type=media_type),
                   {"type": "text", "text": "Extract the fields from this file."}]
        return self._extract_with(system, content, schema, self.resolve_model(model), purpose, audit_hook)

    def run_tools(self, system, messages, tools, max_turns=8, *, model=None, max_tokens=4096,
                  purpose="run_tools", audit_hook=None) -> tuple[str, list[dict[str, Any]]]:
        """Manual function-calling loop: call → run every function_call part → send all
        function_responses back in one user turn → repeat. Same transcript format as AnthropicLlm."""
        types = self._types()
        model = self.resolve_model(model)
        specs = {t.name: t for t in tools}
        decls = [types.FunctionDeclaration(name=t.name, description=t.description,
                                           parameters_json_schema=gemini_schema(t.input_schema)) for t in tools]
        convo = self.to_contents(messages)
        transcript: list[dict[str, Any]] = []
        final_text = ""
        for turn in range(max_turns):
            resp, _, stop = self._generate(model=model, system=system, contents=convo, max_tokens=max_tokens,
                                           tools=decls, purpose=purpose, audit_hook=audit_hook)
            text = self._text(resp)
            if text:
                final_text = text
                transcript.append({"ts": _now_iso(), "turn": turn, "type": "assistant_text", "text": text})
            if stop == "refusal":
                transcript.append({"ts": _now_iso(), "turn": turn, "type": "refusal"})
                break
            calls = [p.function_call for p in self._parts(resp) if getattr(p, "function_call", None)]
            if not calls:
                break
            convo.append(resp.candidates[0].content)  # unchanged: keeps thought signatures
            parts = []
            for fc in calls:
                entry, result = _run_handler(specs, fc.name, dict(fc.args or {}), turn)
                transcript.append(entry)
                payload = ({"error": result["content"]} if entry["is_error"]
                           else {"result": _json_payload(entry["output"])})
                parts.append(types.Part(function_response=types.FunctionResponse(
                    id=getattr(fc, "id", None), name=fc.name, response=payload)))
            convo.append(types.Content(role="user", parts=parts))
        else:
            transcript.append({"ts": _now_iso(), "type": "max_turns_reached", "max_turns": max_turns})
        return final_text, transcript


# ================================================================ Fake

_BP_RE = re.compile(r"(\d{2,3})\s*/\s*(\d{2,3})")
_PULSE_RE = re.compile(r"(?:pulse|脈拍|pul|hr|♥)\D{0,6}(\d{2,3})", re.I)
_LINE_FIELD = {
    "diagnoses": re.compile(r"^\s*(?:diagnos[ie]s|dx|診断名?)\s*[:：]\s*(.+)$", re.I | re.M),
    "medications": re.compile(r"^\s*(?:rx|medications?|処方|薬)\s*[:：]\s*(.+)$", re.I | re.M),
    "follow_ups": re.compile(r"^\s*(?:follow[- ]?ups?|plan|次回)\s*[:：]\s*(.+)$", re.I | re.M),
    "classification": re.compile(r"^\s*(?:classification|判定|結果|result)\s*[:：]\s*(.+)$", re.I | re.M),
    "device": re.compile(r"^\s*(?:device|機器|機種)\s*[:：]\s*(.+)$", re.I | re.M),
    "recorded_at": re.compile(r"^\s*(?:recorded(?: at| date)?|date|記録日時?|測定日時?)\s*[:：]\s*(.+)$", re.I | re.M),
}
_MED_RE = re.compile(r"^(?P<name>.+?)\s*(?P<dose>\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|ml|mL|単位|錠|IU))\s*(?P<schedule>.*)$")
_LAB_RE = re.compile(r"^\s*(LDL|HDL|eGFR|HbA1c|total cholesterol|TC|TG)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*([A-Za-z/%.0-9²]*)",
                     re.I | re.M)


def _split_items(s: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;,、；]", s) if p.strip()]


class FakeLlm(_Base):
    """Deterministic offline provider for tests/dev.

    - `complete`: returns `canned_answer` (may contain `{last_user}`).
    - `extract`: pops `extract_queue` if non-empty, else derives every schema property from
      the input with `extract_rules` (field → regex, group 1) and built-in regexes
      (BP "142/91", "Pulse 72", "Diagnosis:", "Rx:", "Classification:" lines, lab lines);
      missing fields → null / [] / false.
    - `run_tools`: executes `tool_script` [(tool_name, input), ...] in order, then returns
      `final_text`.
    - `vision_extract`: returns `vision_result` (or pops `vision_queue`), else derives from
      `vision_text` like `extract`.
    """

    def __init__(self, canned_answer: str = "(fake LLM) I can't give medical advice, but here is a placeholder answer.",
                 extract_queue: list[dict[str, Any]] | None = None,
                 extract_rules: dict[str, str] | None = None,
                 tool_script: list[tuple[str, dict[str, Any]]] | None = None,
                 final_text: str = "(fake LLM) done",
                 vision_result: dict[str, Any] | None = None,
                 vision_queue: list[dict[str, Any]] | None = None,
                 vision_text: str = "",
                 audit_hook: AuditHook | None = None):
        super().__init__(audit_hook)
        self.model_default = "fake-llm"
        self.model_cheap = "fake-llm-cheap"
        self.canned_answer = canned_answer
        self.extract_queue = list(extract_queue or [])
        self.extract_rules = {k: re.compile(v, re.I | re.M) for k, v in (extract_rules or {}).items()}
        self.tool_script = list(tool_script or [])
        self.final_text = final_text
        self.vision_result = vision_result
        self.vision_queue = list(vision_queue or [])
        self.vision_text = vision_text
        self.calls: list[dict[str, Any]] = []

    def _usage(self, text: str) -> LlmUsage:
        return LlmUsage(input_tokens=max(1, len(text) // 4), output_tokens=10)

    @staticmethod
    def _as_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
        return str(content)

    def complete(self, system, messages, max_tokens=1024, temperature=None, *, model=None,
                 purpose="complete", audit_hook=None) -> LlmResponse:
        last_user = next((self._as_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
        text = self.canned_answer.replace("{last_user}", last_user)
        model = model or self.model_default
        usage = self._usage(system + last_user)
        self.calls.append({"method": "complete", "purpose": purpose})
        self._audit(audit_hook, model, usage, purpose, "end_turn")
        return LlmResponse(text=text, model=model, usage=usage, stop_reason="end_turn")

    def stream(self, system, messages, max_tokens=1024, temperature=None, *, model=None,
               purpose="stream", audit_hook=None) -> Iterator[tuple[str, str]]:
        """The `complete` answer word by word, after one placeholder thought."""
        text = self.complete(system, messages, max_tokens, temperature, model=model, purpose=purpose,
                             audit_hook=audit_hook).text
        yield "thought", "(fake LLM) thinking"
        for word in re.findall(r"\S+\s*", text):
            yield "text", word

    def derive(self, text: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Regex-derive values for every top-level property of `schema` from `text`."""
        out: dict[str, Any] = {}
        for name, prop in (schema.get("properties") or {}).items():
            out[name] = self._derive_field(name, prop, text)
        return out

    def _derive_field(self, name: str, prop: dict[str, Any], text: str) -> Any:
        typ = prop.get("type")
        if isinstance(typ, list):
            typ = next((t for t in typ if t != "null"), None)
        if name in self.extract_rules:
            m = self.extract_rules[name].search(text)
            if not m:
                return [] if typ == "array" else None
            val = m.group(1) if m.groups() else m.group(0)
            return _coerce(val, typ)
        bp = _BP_RE.search(text)
        if name == "systolic":
            return int(bp.group(1)) if bp else None
        if name == "diastolic":
            return int(bp.group(2)) if bp else None
        if name == "pulse":
            m = _PULSE_RE.search(text)
            return int(m.group(1)) if m else None
        if name in ("irregular_icon", "irregular_pulse"):
            return bool(re.search(r"irregular|不規則|IHB", text, re.I))
        if name == "summary":
            return text.strip()[:200] or None
        if name == "medications":
            return [self._med(item) for m in _LINE_FIELD["medications"].finditer(text)
                    for item in _split_items(m.group(1))]
        if name == "lab_values":
            return [{"name": m.group(1), "value": float(m.group(2)), "unit": m.group(3) or None, "date": None}
                    for m in _LAB_RE.finditer(text)]
        if name in ("diagnoses", "follow_ups"):
            return [item for m in _LINE_FIELD[name].finditer(text) for item in _split_items(m.group(1))]
        if name in _LINE_FIELD:
            m = _LINE_FIELD[name].search(text)
            return m.group(1).strip() if m else None
        return {"array": [], "boolean": False}.get(typ)

    @staticmethod
    def _med(item: str) -> dict[str, Any]:
        m = _MED_RE.match(item.strip())
        if not m:
            return {"name": item.strip(), "dose": None, "schedule": None}
        return {"name": m.group("name").strip(), "dose": (m.group("dose") or "").strip() or None,
                "schedule": m.group("schedule").strip() or None}

    def extract(self, system, user_content, schema, model=None, *, tool_name="record",
                purpose="extract", audit_hook=None) -> dict[str, Any]:
        text = self._as_text(user_content)
        result = self.extract_queue.pop(0) if self.extract_queue else self.derive(text, schema)
        self.calls.append({"method": "extract", "purpose": purpose})
        self._audit(audit_hook, model or self.model_cheap, self._usage(system + text), purpose, "tool_use")
        return result

    def vision_extract(self, image_bytes, media_type, system, schema, *, model=None,
                       purpose="vision_extract", audit_hook=None) -> dict[str, Any]:
        if self.vision_queue:
            result = self.vision_queue.pop(0)
        elif self.vision_result is not None:
            result = dict(self.vision_result)
        else:
            result = self.derive(self.vision_text, schema)
        self.calls.append({"method": "vision_extract", "purpose": purpose, "media_type": media_type})
        self._audit(audit_hook, model or self.model_default, LlmUsage(input_tokens=len(image_bytes) // 750,
                                                                     output_tokens=10), purpose, "tool_use")
        return result

    def run_tools(self, system, messages, tools, max_turns=8, *, model=None, max_tokens=4096,
                  purpose="run_tools", audit_hook=None) -> tuple[str, list[dict[str, Any]]]:
        specs = {t.name: t for t in tools}
        transcript: list[dict[str, Any]] = []
        for turn, (name, tool_input) in enumerate(self.tool_script[:max_turns]):
            entry, _ = _run_handler(specs, name, dict(tool_input), turn)
            transcript.append(entry)
            self._audit(audit_hook, model or self.model_default, LlmUsage(10, 10), purpose, "tool_use")
        if len(self.tool_script) > max_turns:
            transcript.append({"ts": _now_iso(), "type": "max_turns_reached", "max_turns": max_turns})
        transcript.append({"ts": _now_iso(), "turn": len(transcript), "type": "assistant_text", "text": self.final_text})
        self.calls.append({"method": "run_tools", "purpose": purpose})
        return self.final_text, transcript


def _coerce(val: str, typ: str | None) -> Any:
    val = val.strip()
    if typ == "integer":
        try:
            return int(float(val))
        except ValueError:
            return None
    if typ == "number":
        try:
            return float(val)
        except ValueError:
            return None
    if typ == "boolean":
        return val.lower() in ("1", "true", "yes", "y", "あり")
    if typ == "array":
        return _split_items(val)
    return val


# ================================================================ factory

def get_llm_provider(audit_hook: AuditHook | None = None) -> LlmProvider:
    """`settings.llm_provider`: "gemini" → GeminiLlm, "anthropic" → AnthropicLlm, else FakeLlm."""
    if settings.llm_provider == "gemini":
        return GeminiLlm(audit_hook=audit_hook)
    if settings.llm_provider == "anthropic":
        return AnthropicLlm(audit_hook=audit_hook)
    return FakeLlm(audit_hook=audit_hook)
