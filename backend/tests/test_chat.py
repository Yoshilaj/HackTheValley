"""Chat routes/services with fake LLM, embeddings and STT."""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api.routes import chat as chat_routes
from app.core.auth import create_access_token
from app.db.models import AssessmentTask, AuditLog, ChatMessage, Notification, Observation, Profile, User
from app.prompts.chat_system import build_context_block
from app.providers.embeddings import FakeEmbeddings
from app.providers.llm import FakeLlm, LlmApiError
from app.providers.storage import LocalStorage
from app.providers.stt import FakeStt
from app.rag.ingest import ingest_corpus

SOURCES = Path(__file__).parent / "fixtures" / "rag" / "sources.json"


@pytest.fixture()
def user(db):
    u = User(email=f"chat-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.flush()
    db.add(Profile(user_id=u.id, language="en", timezone="America/Toronto", display_name="Test",
                   medications=[{"name": "amlodipine", "dose": "5 mg"}]))
    db.commit()
    return u


@pytest.fixture()
def headers(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture()
def fakes(app, db, tmp_path):
    ingest_corpus(db, FakeEmbeddings(), SOURCES)
    state = {"llm": FakeLlm(canned_answer="Home readings are taken twice in the morning [1]."),
             "stt": FakeStt(text="Lately I keep forgetting my appointments and I feel dizzy.")}
    app.dependency_overrides[chat_routes.get_llm] = lambda: state["llm"]
    app.dependency_overrides[chat_routes.get_embeddings] = lambda: FakeEmbeddings()
    app.dependency_overrides[chat_routes.get_stt] = lambda: state["stt"]
    app.dependency_overrides[chat_routes.get_file_storage] = lambda: LocalStorage(tmp_path)
    yield state
    for dep in (chat_routes.get_llm, chat_routes.get_embeddings, chat_routes.get_stt, chat_routes.get_file_storage):
        app.dependency_overrides.pop(dep, None)


def _count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_chat_requires_auth(client):
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_symptom_message_goes_through_the_normal_pipeline(client, db, user, headers, fakes):
    """No red-flag detection: a chest-pain message is answered like any other (RAG + extraction)."""
    res = client.post("/api/chat", json={"message": "I have crushing chest pain and my left arm feels numb"},
                      headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert "red_flags" not in body and body["assessment_hint"] != "emergency"
    assert [c["method"] for c in fakes["llm"].calls] == ["complete", "extract"]
    db.expire_all()
    assert db.scalars(select(Notification).where(Notification.user_id == user.id)).all() == []
    assert not any(o.kind == "red_flag" for o in db.scalars(select(Observation).where(Observation.user_id == user.id)))
    assert [m.role for m in db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id)
                                       .order_by(ChatMessage.created_at))] == ["user", "assistant"]


def test_normal_chat_saves_messages_sources_observations(client, db, user, headers, fakes):
    fakes["llm"] = FakeLlm(canned_answer="Measure twice each morning [1]; you probably have hypertension.",
                           extract_queue=[{"observations": [
                               {"kind": "symptom", "label": "Dizziness", "severity": "mild", "onset": "yesterday",
                                "free_text": "a bit dizzy"},
                               {"kind": "functional_change", "label": "memory_lapse", "severity": None},
                               {"kind": "red_flag", "label": "chest_pain"},  # unknown kind → dropped
                               {"kind": "symptom", "label": ""},
                           ]}, {"observations": [{"kind": "symptom", "label": "dizziness"}]}])
    msg = "How should I measure my home blood pressure? I was a bit dizzy yesterday and forgot my keys."
    res = client.post("/api/chat", json={"message": msg}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sources"] and body["sources"][0]["n"] == 1 and body["sources"][0]["id"].startswith("src_")
    assert body["cited"] == [1]  # the canned answer cites [1] only
    assert "you probably have" not in body["answer"]
    from app.services.guardrails import DISCLAIMER_EN

    assert body["answer"].rstrip().endswith(DISCLAIMER_EN.rstrip())
    assert {(o["kind"], o["label"]) for o in body["observations_created"]} == {
        ("symptom", "dizziness"), ("functional_change", "memory_lapse")}
    assert body["assessment_hint"] == "story_recall"
    assert [c["method"] for c in fakes["llm"].calls] == ["complete", "extract"]

    db.expire_all()
    msgs = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id).order_by(ChatMessage.created_at)).all()
    assert [m.role for m in msgs] == ["user", "assistant"] and msgs[1].sources
    obs = db.scalars(select(Observation).where(Observation.user_id == user.id, Observation.label == "dizziness")).all()
    assert obs[0].source == "chat" and obs[0].source_id == msgs[0].id and obs[0].onset == "yesterday"
    assert _count(db, AuditLog, AuditLog.user_id == user.id, AuditLog.action == "llm_call") == 2

    # second turn: same label within 24 h is deduped; history is passed to the LLM
    res2 = client.post("/api/chat", json={"message": "Still dizzy today."}, headers=headers)
    assert res2.json()["observations_created"] == []

    hist = client.get("/api/chat/history?limit=10", headers=headers).json()
    assert [h["role"] for h in hist] == ["user", "assistant", "user", "assistant"]
    assert hist[1]["sources"][0]["n"] == 1


def test_voice_message_creates_free_speech_task(client, db, user, headers, fakes):
    fakes["llm"] = FakeLlm(extract_queue=[{"observations": [{"kind": "functional_change",
                                                             "label": "missed_appointments"}]}])
    res = client.post("/api/chat/voice", headers=headers, data={"language": "en"},
                      files={"file": ("note.webm", b"\x1aE\xdf\xa3notreallyaudio", "audio/webm")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transcript"].startswith("Lately I keep forgetting")
    assert body["observations_created"][0]["label"] == "missed_appointments"
    db.expire_all()
    task = db.get(AssessmentTask, body["task_id"])
    assert task.task_type == "free_speech" and task.language == "en"
    assert task.features["lexical"]["word_count"] > 5 and "temporal" in task.features
    assert task.recording_quality and task.raw_url
    user_msg = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.role == "user")).one()
    assert user_msg.audio_url == task.raw_url


def test_voice_rejects_empty_transcript_and_bad_type(client, headers, fakes):
    fakes["stt"] = FakeStt(text="")
    res = client.post("/api/chat/voice", headers=headers, files={"file": ("a.webm", b"xx", "audio/webm")})
    assert res.status_code == 422
    res = client.post("/api/chat/voice", headers=headers, files={"file": ("a.txt", b"xx", "text/plain")})
    assert res.status_code == 415


def test_context_block_is_compact(db, user):
    db.add(Observation(user_id=user.id, kind="symptom", label="palpitations", source="chat"))
    db.commit()
    block = build_context_block(db, user)
    assert len(block) < 1500
    assert "palpitations" in block and "amlodipine 5 mg" in block


def test_sources_endpoint(client, db, fakes):
    res = client.get("/api/sources?pathway=cardio")
    assert res.status_code == 200
    rows = {r["id"]: r for r in res.json()}
    assert rows["src_test_bp_jats"]["chunk_count"] > 0
    assert "src_test_memory_html" not in rows


def test_ingest_endpoint_admin_gate(client, monkeypatch, fakes):
    from app.core.config import settings

    monkeypatch.setattr(settings, "dev_mode", False)
    assert client.post("/api/ingest", json={"text": "abc", "source": "src_gate"}).status_code == 403
    ok = client.post("/api/ingest", json={"text": "abc", "source": "src_gate"},
                     headers={"X-Admin-Secret": settings.admin_secret})
    assert ok.status_code == 200 and ok.json()["chunks_added"] == 1


def test_system_prompt_forbids_crisis_footers():
    """Review round 2: a story-recall answer appended 9-8-8 unprompted. The prompt forbids the model
    from adding crisis lines or emergency footers; the code never adds any."""
    from app.prompts.chat_system import system_prompt

    p = system_prompt("en")
    assert "NEVER append phone numbers" in p and "story-recall" in p
    assert "9-8-8" not in p and "911" not in p


def test_plain_question_gets_no_crisis_text_from_code():
    """The output guard never adds 9-8-8 / emergency guidance (FakeLlm answer + guard)."""
    from app.services.guardrails import output_guard

    assert "9-8-8" not in output_guard("Your recall score was 7 of 10 [1].", "en")


def _stream(client, headers, **body):
    res = client.post("/api/chat/stream", json=body, headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in res.text.splitlines() if line]


def test_chat_stream_sends_progress_guarded_deltas_then_saved_turn(client, db, user, headers, fakes):
    fakes["llm"] = FakeLlm(canned_answer="Measure twice each morning [1]. You probably have hypertension. Rest well.",
                           extract_queue=[{"observations": [{"kind": "symptom", "label": "dizziness"}]}])
    events = _stream(client, headers, message="How do I measure my blood pressure? I feel dizzy.",
                     new_conversation=True)
    types_ = [e["type"] for e in events]
    assert types_[:3] == ["status", "status", "thought"]
    assert [e["stage"] for e in events[:2]] == ["searching", "thinking"]
    assert set(types_[3:-2]) == {"delta"} and types_[-2:] == ["answer", "done"]

    streamed = "".join(e["text"] for e in events if e["type"] == "delta")
    final, done = events[-2], events[-1]
    assert "probably have" not in streamed  # the guard rewrites before anything is sent
    assert final["answer"].startswith(streamed) and final["answer"] == done["answer"]
    from app.services.guardrails import DISCLAIMER_EN

    assert done["answer"].rstrip().endswith(DISCLAIMER_EN)
    assert final["sources"] and done["sources"][0]["n"] == 1 and done["cited"] == [1]
    assert [o["label"] for o in done["observations_created"]] == ["dizziness"]
    assert done["conversation_id"] and done["message_id"]

    db.expire_all()
    msgs = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id).order_by(ChatMessage.created_at)).all()
    assert [m.role for m in msgs] == ["user", "assistant"] and msgs[1].content == done["answer"]
    assert msgs[1].id == done["message_id"] and msgs[1].conversation_id == done["conversation_id"]


def test_chat_stream_chest_pain_is_an_ordinary_turn(client, user, headers, fakes):
    events = _stream(client, headers, message="I have crushing chest pain")
    assert [e["type"] for e in events][-2:] == ["answer", "done"]
    assert "red_flags" not in events[-1] and fakes["llm"].calls


def test_chat_stream_rejects_before_streaming(client, headers, fakes):
    res = client.post("/api/chat/stream", json={"message": "hi", "conversation_id": str(uuid.uuid4())},
                      headers=headers)
    assert res.status_code == 404
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 401


def test_chat_stream_llm_failure_mid_answer_ends_with_apology(client, db, user, headers, fakes):
    class Flaky(FakeLlm):
        def stream(self, *args, **kwargs):
            yield "text", "Half an answer. "
            raise LlmApiError("gemini API error 429", code=429)

    fakes["llm"] = Flaky()
    events = _stream(client, headers, message="What is a normal resting heart rate?")
    assert events[-2]["type"] == "answer" and events[-2]["answer"].startswith("Sorry")
    assert events[-1]["type"] == "done" and events[-1]["answer"] == events[-2]["answer"]


def test_guarded_stream_never_sends_unfinished_or_unguarded_sentences():
    from app.services.chat_service import _GuardedStream
    from app.services.guardrails import rewrite_phrasing

    text = "Readings vary. You should stop taking amlodipine today. Take 50 mg only if told.\nSee 2.5 ok"
    g = _GuardedStream()
    out = "".join(g.feed(text[i:i + 7]) for i in range(0, len(text), 7))
    assert "should stop" not in out and "Take 50 mg" not in out
    assert out == rewrite_phrasing(text[:text.rindex("\n") + 1])  # the unfinished last line is held back
