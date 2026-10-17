"""LLM provider: FakeLlm behaviour, AnthropicLlm request shaping with a stub client (no network)."""

from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.providers.llm import (
    AnthropicLlm,
    FakeLlm,
    LlmConfigError,
    LlmError,
    ToolSpec,
    get_llm_provider,
    transcribe_document,
)

SCHEMA = {"type": "object", "properties": {
    "systolic": {"type": "integer"}, "diastolic": {"type": "integer"}, "pulse": {"type": ["integer", "null"]},
    "mood": {"type": "string"}, "tags": {"type": "array"}}}


def test_fake_complete_and_audit_hook():
    seen = []
    llm = FakeLlm(canned_answer="echo: {last_user}", audit_hook=seen.append)
    r = llm.complete("sys", [{"role": "user", "content": "hello"}], max_tokens=10)
    assert r.text == "echo: hello" and r.model == "fake-llm"
    assert seen[0]["purpose"] == "complete" and seen[0]["input_tokens"] > 0


def test_fake_extract_queue_then_regex():
    llm = FakeLlm(extract_queue=[{"systolic": 1}], extract_rules={"mood": r"mood[:=]\s*(\w+)"})
    assert llm.extract("s", "anything", SCHEMA) == {"systolic": 1}
    out = llm.extract("s", "BP 142/91 pulse 70, mood: tired", SCHEMA)
    assert out == {"systolic": 142, "diastolic": 91, "pulse": 70, "mood": "tired", "tags": []}
    assert llm.extract("s", "nothing here", SCHEMA)["systolic"] is None


def test_fake_run_tools_scripted_order():
    calls = []
    tools = [
        ToolSpec("search_clinics", "find clinics", {"type": "object"}, lambda i: calls.append(("s", i)) or [{"name": "A"}]),
        ToolSpec("send_email", "email", {"type": "object"}, lambda i: calls.append(("e", i)) or "sent"),
        ToolSpec("boom", "fails", {"type": "object"}, lambda i: 1 / 0),
    ]
    llm = FakeLlm(tool_script=[("search_clinics", {"q": "cardio"}), ("boom", {}), ("send_email", {"to": "x"}),
                               ("missing", {})], final_text="booked")
    text, transcript = llm.run_tools("sys", [{"role": "user", "content": "book"}], tools, max_turns=8)
    assert text == "booked"
    assert calls == [("s", {"q": "cardio"}), ("e", {"to": "x"})]
    tool_entries = [t for t in transcript if t["type"] == "tool"]
    assert [t["tool"] for t in tool_entries] == ["search_clinics", "boom", "send_email", "missing"]
    assert [t["is_error"] for t in tool_entries] == [False, True, False, True]
    assert tool_entries[0]["output"] == [{"name": "A"}] and "ts" in tool_entries[0]


def test_fake_vision_and_transcribe(tmp_path):
    llm = FakeLlm(vision_result={"systolic": 120})
    assert llm.vision_extract(b"img", "image/png", "s", SCHEMA) == {"systolic": 120}
    assert FakeLlm(vision_text="118/76").vision_extract(b"x", "image/png", "s", SCHEMA)["diastolic"] == 76
    assert transcribe_document("plain") == "plain"
    assert transcribe_document(b"bytes text") == "bytes text"
    from pathlib import Path
    pdf = (Path(__file__).parent / "fixtures" / "doctor_note.pdf").read_bytes()
    assert "Amlodipine" in llm.transcribe_document(pdf)


def test_factory(monkeypatch):
    assert isinstance(get_llm_provider(), FakeLlm)
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    assert isinstance(get_llm_provider(), AnthropicLlm)


def test_anthropic_without_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = AnthropicLlm()
    assert llm.model_default == "claude-sonnet-5-5" and llm.model_cheap == "claude-haiku-5-5"
    with pytest.raises(LlmConfigError, match="ANTHROPIC_API_KEY"):
        llm.complete("sys", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------- stubbed Anthropic client

class StubMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def resp(content, stop="end_turn", model="claude-sonnet-5-5"):
    return NS(content=content, stop_reason=stop, model=model,
              usage=NS(input_tokens=11, output_tokens=7, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def text(t):
    return NS(type="text", text=t)


def tool_use(name, inp, id_="tu_1"):
    return NS(type="tool_use", name=name, input=inp, id=id_)


def stub_llm(responses, **kw):
    llm = AnthropicLlm(api_key="test", **kw)
    stub = StubMessages(responses)
    llm._client = NS(beta=NS(messages=stub))
    return llm, stub


def test_anthropic_complete_request_shape():
    audits = []
    llm, stub = stub_llm([resp([text("hello")])], audit_hook=audits.append)
    r = llm.complete("SYSTEM", [{"role": "user", "content": "hi"}], max_tokens=50, temperature=0.2)
    assert r.text == "hello" and r.usage.input_tokens == 11
    req = stub.requests[0]
    assert req["model"] == "claude-sonnet-5-5"
    assert req["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert "temperature" not in req  # rejected by 5.x models
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert audits == [{"model": "claude-sonnet-5-5", "purpose": "complete", "stop_reason": "end_turn",
                       "input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 0}]


def test_anthropic_extract_uses_single_tool_and_reprompts():
    llm, stub = stub_llm([resp([text("thinking aloud")]), resp([tool_use("record", {"systolic": 130})], "tool_use",
                                                                model="claude-haiku-5-5")])
    out = llm.extract("Extract BP", "130/80", SCHEMA)
    assert out == {"systolic": 130}
    first = stub.requests[0]
    assert first["model"] == "claude-haiku-5-5" and "fallbacks" not in first
    assert first["tools"][0]["name"] == "record" and first["tools"][0]["input_schema"] == SCHEMA
    assert "tool_choice" not in first  # forced tool_choice is a 400 on Sonnet 5.5
    assert len(stub.requests[1]["messages"]) == 3


def test_anthropic_extract_refusal_raises():
    llm, _ = stub_llm([resp([], "refusal")])
    with pytest.raises(LlmError):
        llm.extract("s", "x", SCHEMA)


def test_anthropic_run_tools_loop():
    llm, stub = stub_llm([
        resp([text("Searching"), tool_use("lookup", {"q": "a"}, "t1"), tool_use("lookup", {"q": "b"}, "t2")], "tool_use"),
        resp([text("All done")]),
    ])
    tools = [ToolSpec("lookup", "d", {"type": "object"}, lambda i: {"found": i["q"]})]
    final, transcript = llm.run_tools("sys", [{"role": "user", "content": "go"}], tools)
    assert final == "All done"
    results = stub.requests[1]["messages"][-1]
    assert results["role"] == "user" and [r["tool_use_id"] for r in results["content"]] == ["t1", "t2"]
    assert [t["output"] for t in transcript if t["type"] == "tool"] == [{"found": "a"}, {"found": "b"}]
