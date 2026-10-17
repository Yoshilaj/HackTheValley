"""Chat routes/services with fake LLM, embeddings and STT."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api.routes import chat as chat_routes
from app.core.auth import create_access_token
from app.db.models import AssessmentTask, AuditLog, ChatMessage, Notification, Observation, Profile, User
from app.prompts.chat_system import build_context_block
from app.providers.embeddings import FakeEmbeddings
from app.providers.llm import FakeLlm
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


def test_red_flag_short_circuits_without_llm(client, db, user, headers, fakes):
    res = client.post("/api/chat", json={"message": "I have crushing chest pain and my left arm feels numb"},
                      headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["red_flags"] == ["chest_pain", "arm_weakness"]
    assert body["assessment_hint"] == "emergency"
    assert "911" in body["answer"] and body["sources"] == [] and body["cited"] == []
    assert fakes["llm"].calls == []  # no LLM call of any kind
    db.expire_all()
    notes = db.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert [n.kind for n in notes] == ["emergency"] and notes[0].payload["emergency_number"] == "911"
    obs = db.scalars(select(Observation).where(Observation.user_id == user.id)).all()
    assert {(o.kind, o.label, o.severity, o.source) for o in obs} == {
        ("red_flag", "chest_pain", "urgent", "chat"), ("red_flag", "arm_weakness", "urgent", "chat")}
    assert [m.role for m in db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id)
                                       .order_by(ChatMessage.created_at))] == ["user", "assistant"]


def test_red_flag_japanese_text_still_gets_canadian_numbers(client, db, user, headers, fakes):
    res = client.post("/api/chat", json={"message": "急に胸が痛い", "language": "ja"}, headers=headers)
    body = res.json()
    assert body["red_flags"] == ["chest_pain"] and "911" in body["answer"] and "811" in body["answer"]
    assert "119" not in body["answer"]
    assert fakes["llm"].calls == []


def test_normal_chat_saves_messages_sources_observations(client, db, user, headers, fakes):
    fakes["llm"] = FakeLlm(canned_answer="Measure twice each morning [1]; you probably have hypertension.",
                           extract_queue=[{"observations": [
                               {"kind": "symptom", "label": "Dizziness", "severity": "mild", "onset": "yesterday",
                                "free_text": "a bit dizzy"},
                               {"kind": "functional_change", "label": "memory_lapse", "severity": None},
                               {"kind": "red_flag", "label": "chest_pain"},  # never accepted from extraction
                               {"kind": "symptom", "label": ""},
                           ]}, {"observations": [{"kind": "symptom", "label": "dizziness"}]}])
    msg = "How should I measure my home blood pressure? I was a bit dizzy yesterday and forgot my keys."
    res = client.post("/api/chat", json={"message": msg}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["red_flags"] == []
    assert body["sources"] and body["sources"][0]["n"] == 1 and body["sources"][0]["id"].startswith("src_")
    assert body["cited"] == [1]  # the canned answer cites [1] only
    assert "you probably have" not in body["answer"]
    assert body["answer"].rstrip().endswith("For sudden or severe symptoms, call 911.")
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


def test_system_prompt_makes_crisis_numbers_conditional():
    """Review round 2: a story-recall answer appended 9-8-8 unprompted. The code adds emergency text
    only for detected red flags; the prompt forbids the model from adding crisis lines otherwise."""
    from app.prompts.chat_system import system_prompt

    p = system_prompt("en")
    assert "CONDITIONAL" in p and "NEVER add 9-8-8" in p and "story-recall" in p
    assert "Only if the user's own message" in p and "mentions thoughts of suicide or self-harm" in p


def test_plain_question_gets_no_crisis_text_from_code():
    """No red flag -> the code path never adds 9-8-8 / emergency guidance (FakeLlm answer + guard)."""
    from app.services.guardrails import detect_red_flags, output_guard

    text = "How did I do on the story recall task this week? I felt a bit sad about the story."
    assert detect_red_flags(text, "en") == []
    assert "9-8-8" not in output_guard("Your recall score was 7 of 10 [1].", "en")
