"""GeminiLlm with a fake google-genai client (no network)."""

import base64
import json

import pytest
from google.genai import errors, types

from app.core.config import settings
from app.providers import llm as llm_mod
from app.providers.llm import (CHEAP_MODEL, DEFAULT_MODEL, GeminiLlm, LlmConfigError, LlmError, ToolSpec,
                               gemini_schema, get_llm_provider)


def _resp(parts, finish="STOP", prompt=10, out=5, thoughts=0, model="gemini-test-001"):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=thoughts),
        model_version=model,
    )


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def generate_content_stream(self, *, model, contents, config):
        """Each response is an exception or a list of chunks (an exception in the list is raised mid-stream)."""
        self.calls.append({"model": model, "contents": list(contents), "config": config, "stream": True})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        for chunk in r:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


def _llm(responses, **kw):
    client = FakeClient(responses)
    return GeminiLlm(model_default="gem-main", model_cheap="gem-lite", client=client, **kw), client.models


def test_missing_key_raises_config_error(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    llm = GeminiLlm()
    with pytest.raises(LlmConfigError):
        llm.complete("s", [{"role": "user", "content": "hi"}])


def test_factory_selects_gemini(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    p = get_llm_provider()
    assert isinstance(p, GeminiLlm)
    assert p.model_default == settings.gemini_model and p.model_cheap == settings.gemini_cheap_model


def test_model_name_translation():
    llm, _ = _llm([])
    assert llm.resolve_model(None) == "gem-main"
    assert llm.resolve_model(CHEAP_MODEL) == "gem-lite"
    assert llm.resolve_model("claude-haiku-4-5") == "gem-lite"
    assert llm.resolve_model(DEFAULT_MODEL) == "gem-main"
    assert llm.resolve_model("claude-opus-5-5") == "gem-main"
    assert llm.resolve_model(llm.model_cheap) == "gem-lite"
    assert llm.resolve_model("gemini-2.5-pro") == "gemini-2.5-pro"


def test_message_conversion():
    llm, _ = _llm([])
    img = base64.b64encode(b"\x89PNGdata").decode()
    contents = llm.to_contents([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "let me look"},
                                          {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": 1}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "found"}]},
        {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                   "data": img}},
                                     {"type": "text", "text": "and this"}]},
    ])
    assert [c.role for c in contents] == ["user", "model", "user"]  # consecutive user turns merged
    assert contents[0].parts[0].text == "hello"
    fc = contents[1].parts[1].function_call
    assert (fc.name, fc.id, fc.args) == ("lookup", "t1", {"q": 1})
    fr = contents[2].parts[0].function_response
    assert (fr.name, fr.id, fr.response) == ("lookup", "t1", {"result": "found"})
    assert contents[2].parts[1].inline_data.data == b"\x89PNGdata"
    assert contents[2].parts[1].inline_data.mime_type == "image/png"
    assert contents[2].parts[2].text == "and this"


def test_complete_text_usage_and_audit():
    audits = []
    llm, models = _llm([_resp([types.Part(text="thinking...", thought=True), types.Part(text="Hi there")],
                              prompt=42, out=7, thoughts=3)], audit_hook=audits.append)
    r = llm.complete("be nice", [{"role": "user", "content": "hi"}], max_tokens=100, temperature=0.2,
                     model=CHEAP_MODEL, purpose="chat")
    assert r.text == "Hi there" and r.stop_reason == "end_turn" and r.model == "gemini-test-001"
    assert (r.usage.input_tokens, r.usage.output_tokens) == (42, 10)
    call = models.calls[0]
    assert call["model"] == "gem-lite"
    assert call["config"].system_instruction == "be nice"
    assert call["config"].max_output_tokens == 100 and call["config"].temperature == 0.2
    assert audits == [{"model": "gemini-test-001", "purpose": "chat", "stop_reason": "end_turn",
                       "input_tokens": 42, "output_tokens": 10, "cache_read_input_tokens": 0}]


def test_temperature_not_sent_to_gemini3():
    llm = GeminiLlm(model_default="gemini-3.5-flash", model_cheap="x", client=FakeClient([_resp([types.Part(text="ok")])]))
    llm.complete("s", [{"role": "user", "content": "hi"}], temperature=0.1)
    assert llm.client.models.calls[0]["config"].temperature is None


def test_refusal_and_max_tokens_stop_reasons():
    llm, _ = _llm([_resp([], finish="SAFETY"), _resp([types.Part(text="trunc")], finish="MAX_TOKENS")])
    assert llm.complete("s", [{"role": "user", "content": "x"}]).stop_reason == "refusal"
    assert llm.complete("s", [{"role": "user", "content": "x"}]).stop_reason == "max_tokens"


SCHEMA = {"type": "object", "properties": {
    "systolic": {"type": ["integer", "null"], "default": None},
    "severity": {"type": ["string", "null"], "enum": ["mild", "severe", None]},
    "items": {"type": "array", "items": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["a", None]}}}},
}, "required": ["systolic"], "$schema": "https://json-schema.org/draft/2020-12/schema"}


def test_gemini_schema_sanitising():
    g = gemini_schema(SCHEMA)
    assert "$schema" not in g and "default" not in g["properties"]["systolic"]
    assert g["properties"]["severity"] == {"type": ["string", "null"], "enum": ["mild", "severe"]}
    assert g["properties"]["items"]["items"]["properties"]["kind"] == {"type": ["string", "null"], "enum": ["a"]}
    assert SCHEMA["properties"]["severity"]["enum"][-1] is None  # input untouched


def test_extract_structured_output():
    audits = []
    llm, models = _llm([_resp([types.Part(text='{"systolic": 142, "severity": "mild", "items": []}')])],
                       audit_hook=audits.append)
    out = llm.extract("Extract.", "BP 142/91", SCHEMA, purpose="doc")
    assert out == {"systolic": 142, "severity": "mild", "items": []}
    cfg = models.calls[0]["config"]
    assert models.calls[0]["model"] == "gem-lite"  # extract defaults to the cheap model
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == gemini_schema(SCHEMA)
    assert audits[0]["purpose"] == "doc"


def test_extract_reprompts_once_then_parses_embedded_json():
    llm, models = _llm([_resp([types.Part(text="sorry, no")]),
                        _resp([types.Part(text='Here: {"systolic": 120} done')])])
    assert llm.extract("Extract.", "x", SCHEMA) == {"systolic": 120}
    assert len(models.calls) == 2
    second = models.calls[1]["contents"]
    assert [c.role for c in second] == ["user", "model", "user"]


def test_extract_falls_back_when_schema_rejected():
    bad = errors.ClientError(400, {"error": {"code": 400, "message": "Invalid JSON payload: response_json_schema "
                                                                      "unsupported", "status": "INVALID_ARGUMENT"}})
    llm, models = _llm([bad, _resp([types.Part(text='{"systolic": 99}')])])
    assert llm.extract("Extract.", "x", SCHEMA) == {"systolic": 99}
    assert models.calls[1]["config"].response_json_schema is None
    assert "JSON Schema" in models.calls[1]["config"].system_instruction


def test_extract_refusal_and_garbage_raise():
    llm, _ = _llm([_resp([], finish="SAFETY")])
    with pytest.raises(LlmError):
        llm.extract("Extract.", "x", SCHEMA)
    llm, _ = _llm([_resp([types.Part(text="no")]), _resp([types.Part(text="still no")])])
    with pytest.raises(LlmError):
        llm.extract("Extract.", "x", SCHEMA)


def test_vision_extract_inline_bytes():
    llm, models = _llm([_resp([types.Part(text='{"systolic": 130}')])])
    out = llm.vision_extract(b"\xff\xd8jpeg", "image/jpeg", "Read the BP monitor.", SCHEMA)
    assert out == {"systolic": 130}
    parts = models.calls[0]["contents"][0].parts
    assert parts[0].inline_data.data == b"\xff\xd8jpeg" and parts[0].inline_data.mime_type == "image/jpeg"
    assert models.calls[0]["model"] == "gem-main"
    with pytest.raises(LlmError):
        llm.vision_extract(b"x", "text/plain", "s", SCHEMA)


def test_run_tools_two_turn_loop():
    audits = []
    seen = []

    def weather(a):
        seen.append(("weather", a))
        return {"temp_c": 12}

    def boom(a):
        raise ValueError("nope")

    tools = [ToolSpec("weather", "Weather.", {"type": "object", "properties": {"city": {"type": "string"}},
                                              "required": ["city"]}, weather),
             ToolSpec("boom", "Fails.", {"type": "object", "properties": {}}, boom)]
    turn1 = _resp([types.Part(text="Checking."),
                   types.Part(function_call=types.FunctionCall(id="c1", name="weather", args={"city": "Toronto"}),
                              thought_signature=b"sig-1"),
                   types.Part(function_call=types.FunctionCall(id="c2", name="boom", args={}))],
                  prompt=20, out=8)
    turn2 = _resp([types.Part(text="It is 12C in Toronto.")], prompt=40, out=6)
    llm, models = _llm([turn1, turn2], audit_hook=audits.append)
    final, transcript = llm.run_tools("sys", [{"role": "user", "content": "weather?"}], tools, max_turns=4,
                                      purpose="booking_agent")
    assert final == "It is 12C in Toronto."
    assert seen == [("weather", {"city": "Toronto"})]
    assert [(e["type"], e.get("tool")) for e in transcript] == [
        ("assistant_text", None), ("tool", "weather"), ("tool", "boom"), ("assistant_text", None)]
    tool_entry = transcript[1]
    assert set(tool_entry) == {"ts", "turn", "type", "tool", "input", "output", "is_error"}
    assert tool_entry["output"] == {"temp_c": 12} and tool_entry["is_error"] is False
    assert transcript[2]["is_error"] is True and "ValueError" in transcript[2]["output"]
    # second request carries the raw model turn (thought signature kept) + both function responses
    second = models.calls[1]["contents"]
    assert [c.role for c in second] == ["user", "model", "user"]
    assert second[1].parts[1].thought_signature == b"sig-1"
    frs = [p.function_response for p in second[2].parts]
    assert [(f.id, f.name) for f in frs] == [("c1", "weather"), ("c2", "boom")]
    assert frs[0].response == {"result": {"temp_c": 12}} and "error" in frs[1].response
    cfg = models.calls[0]["config"]
    assert cfg.automatic_function_calling.disable is True
    decl_names = [d.name for d in cfg.tools[0].function_declarations]
    assert decl_names == ["weather", "boom"]
    assert [a["stop_reason"] for a in audits] == ["tool_use", "end_turn"]
    assert [a["input_tokens"] for a in audits] == [20, 40]
    assert all(a["purpose"] == "booking_agent" for a in audits)
    json.dumps(transcript)  # JSON-safe like AnthropicLlm's


def test_run_tools_max_turns():
    call = lambda: _resp([types.Part(function_call=types.FunctionCall(id="x", name="t", args={}))])  # noqa: E731
    llm, _ = _llm([call(), call()])
    tools = [ToolSpec("t", "t", {"type": "object", "properties": {}}, lambda a: "ok")]
    _, transcript = llm.run_tools("s", [{"role": "user", "content": "go"}], tools, max_turns=2)
    assert transcript[-1]["type"] == "max_turns_reached"


def test_transcribe_document_passthrough():
    llm, _ = _llm([])
    assert llm.transcribe_document(b"plain text") == "plain text"
    assert llm_mod.GeminiLlm.transcribe_document is llm_mod._Base.transcribe_document


def test_api_errors_become_llm_errors_and_chat_degrades(monkeypatch):
    quota = errors.ClientError(429, {"error": {"code": 429, "message": "You exceeded your current quota",
                                               "status": "RESOURCE_EXHAUSTED"}})
    llm, _ = _llm([quota])
    with pytest.raises(llm_mod.LlmApiError) as ei:
        llm.complete("sys", [{"role": "user", "content": "hi"}])
    assert ei.value.code == 429 and isinstance(ei.value, LlmError) and not isinstance(ei.value, LlmConfigError)

    from app.rag.store import Hit
    from app.services.rag_service import answer_with_citations

    busy = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
    monkeypatch.setattr(GeminiLlm, "TRANSIENT_RETRY_DELAY_S", 0.0)
    llm, models = _llm([busy, _resp([types.Part(text="ok after retry")])])  # one transient 5xx is retried
    assert llm.complete("sys", [{"role": "user", "content": "hi"}]).text == "ok after retry"
    assert len(models.calls) == 2
    assert models.calls[0]["config"].max_output_tokens == 1024  # test model names are not thinking models
    llm, _ = _llm([busy, busy])
    hit = Hit(chunk_id="c1", source_id="s1", text="Home BP of 135/85 or higher.", score=1.0,
              source={"id": "s1", "title": "Guideline", "year": 2020, "url": None})
    out = answer_with_citations(None, llm, "BP threshold?", [hit], "", "en")
    assert out["answer"].startswith("Sorry") and out["model"] is None and len(out["sources"]) == 1


def test_thinking_models_get_output_allowance():
    llm, models = _llm([_resp([types.Part(text="ok")])])
    llm.complete("s", [{"role": "user", "content": "hi"}], max_tokens=1024, model="gemini-3.5-flash")
    assert models.calls[0]["config"].max_output_tokens == 1024 + GeminiLlm.THINKING_ALLOWANCE


def test_stream_yields_thoughts_then_text_and_audits():
    audits = []
    llm, models = _llm([[_resp([types.Part(text="Planning the answer", thought=True)], finish=None),
                         _resp([types.Part(text="Hi ")], finish=None),
                         _resp([types.Part(text="there")], prompt=42, out=7, thoughts=3)]], audit_hook=audits.append)
    events = list(llm.stream("be nice", [{"role": "user", "content": "hi"}], model="gemini-3.5-flash", purpose="chat"))
    assert events == [("thought", "Planning the answer"), ("text", "Hi "), ("text", "there")]
    assert models.calls[0]["stream"] and models.calls[0]["config"].thinking_config.include_thoughts
    assert audits == [{"model": "gemini-test-001", "purpose": "chat", "stop_reason": "end_turn",
                       "input_tokens": 42, "output_tokens": 10, "cache_read_input_tokens": 0}]

    llm, models = _llm([[_resp([types.Part(text="ok")])]])
    assert list(llm.stream("s", [{"role": "user", "content": "hi"}])) == [("text", "ok")]
    assert models.calls[0]["config"].thinking_config is None  # not a thinking model


def test_stream_retries_5xx_only_before_output(monkeypatch):
    busy = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
    monkeypatch.setattr(GeminiLlm, "TRANSIENT_RETRY_DELAY_S", 0.0)
    llm, models = _llm([busy, [_resp([types.Part(text="ok after retry")])]])
    assert list(llm.stream("s", [{"role": "user", "content": "hi"}])) == [("text", "ok after retry")]
    assert len(models.calls) == 2

    llm, models = _llm([[_resp([types.Part(text="Half an ")], finish=None), busy], [_resp([types.Part(text="x")])]])
    seen = []
    with pytest.raises(llm_mod.LlmApiError) as ei:
        for event in llm.stream("s", [{"role": "user", "content": "hi"}]):
            seen.append(event)
    assert ei.value.code == 503 and seen == [("text", "Half an ")] and len(models.calls) == 1
