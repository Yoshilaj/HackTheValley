"""Advocate chat (BACKEND_PLAN C2): text/voice → RAG-cited answer → output guard → Observations.

Turn pipeline (:func:`handle_message`):
  a. save the user ChatMessage;
  b. compact health-context block (``app.prompts.chat_system.build_context_block``);
  c. retrieve k=6 research chunks (pathway inferred from keywords) → cited answer → output guard;
  d. structured extraction with the cheap model → Observations (deduped vs. last 24 h);
  e. every LLM call audited via ``llm_audit_hook``; the turn is committed at the end.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.security import audit
from app.db.models import AssessmentTask, ChatConversation, ChatMessage, Observation, User
from app.prompts.chat_system import build_context_block
from app.prompts.extraction import EXTRACTABLE_KINDS, OBSERVATION_SCHEMA, SEVERITIES, extraction_system
from app.providers.embeddings import EmbeddingProvider
from app.providers.llm import LlmConfigError, LlmError, LlmProvider
from app.rag.ingest import detect_language
from app.services import llm_audit_hook, rag_service
from app.services.guardrails import output_guard, rewrite_phrasing

log = logging.getLogger(__name__)

HISTORY_TURNS = 6
DEDUPE_HOURS = 24
FREE_SPEECH_PROTOCOL = "chat_free_speech_v1"

_HINTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("story_recall", frozenset({"memory_lapse", "confusion", "getting_lost", "repeating_questions",
                                "missed_appointments", "trouble_managing_money", "trouble_managing_medication",
                                "word_finding_difficulty"})),
    ("bp_and_rhythm_check", frozenset({"palpitations", "irregular_heartbeat_feeling", "dizziness",
                                       "breathlessness", "headache"})),
)


@dataclass
class ChatTurn:
    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    cited: list[int] = field(default_factory=list)  # the ``n`` of sources the answer actually cites
    observations_created: list[Observation] = field(default_factory=list)
    assessment_hint: str | None = None
    message_id: str | None = None
    user_message_id: str | None = None
    language: str = "en"
    transcript: str | None = None
    task_id: str | None = None
    conversation_id: str | None = None


def resolve_language(text: str, language: str | None, user: User | None = None) -> str:
    """Explicit ``language`` > script of the message > profile language > 'en' (Canada)."""
    if language:
        return "ja" if language.lower().startswith("ja") else "en"
    if text and text.strip():
        return detect_language(text)
    prof = user.profile if user else None
    return "ja" if ((prof.language if prof else None) or "en").startswith("ja") else "en"


def _snake(label: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z぀-ヿ一-鿿]+", "_", (label or "").strip().lower()).strip("_")
    return s[:100]


def _recent_history(db: Session, user_id: str, conversation_id: str | None = None,
                    limit: int = HISTORY_TURNS) -> list[dict[str, str]]:
    """Last turns the model sees: only this conversation's, so separate chats don't leak into each
    other. Without a conversation (iOS Shortcut, old clients) it is the user's unthreaded messages."""
    q = select(ChatMessage).where(ChatMessage.user_id == user_id, ChatMessage.role.in_(("user", "assistant")))
    q = q.where(ChatMessage.conversation_id == conversation_id if conversation_id
                else ChatMessage.conversation_id.is_(None))
    rows = db.scalars(q.order_by(ChatMessage.created_at.desc()).limit(limit)).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


# ---------------------------------------------------------------- conversations

class ConversationNotFound(LookupError):
    """The conversation doesn't exist or belongs to another user (the API answers 404 either way)."""


TITLE_MAX = 60


def conversation_title(text: str) -> str:
    """Sidebar title from the first message: first line, trimmed to ~60 characters on a word break."""
    line = " ".join((text or "").split())
    if len(line) <= TITLE_MAX:
        return line or "New chat"
    cut = line[:TITLE_MAX].rsplit(" ", 1)[0] or line[:TITLE_MAX]
    return cut.rstrip(" ,.;:") + "…"


def get_conversation(db: Session, user: User, conversation_id: str) -> ChatConversation:
    conv = db.get(ChatConversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise ConversationNotFound(conversation_id)
    return conv


def _open_conversation(db: Session, user: User, conversation_id: str | None, first_text: str) -> ChatConversation:
    """The user's conversation ``conversation_id``, or a new one titled from ``first_text``."""
    if conversation_id:
        return get_conversation(db, user, conversation_id)
    conv = ChatConversation(user_id=user.id, title=conversation_title(first_text))
    db.add(conv)
    db.flush()
    return conv


def list_conversations(db: Session, user: User, limit: int = 100) -> list[dict[str, Any]]:
    """The user's conversations, most recently active first, with their message counts."""
    counts = (select(ChatMessage.conversation_id, func.count(ChatMessage.id).label("n"))
              .where(ChatMessage.user_id == user.id, ChatMessage.conversation_id.is_not(None))
              .group_by(ChatMessage.conversation_id).subquery())
    rows = db.execute(select(ChatConversation, func.coalesce(counts.c.n, 0))
                      .outerjoin(counts, counts.c.conversation_id == ChatConversation.id)
                      .where(ChatConversation.user_id == user.id)
                      .order_by(ChatConversation.updated_at.desc()).limit(limit)).all()
    return [{"id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at,
             "message_count": n} for c, n in rows]


def conversation_messages(db: Session, user: User, conversation_id: str) -> list[ChatMessage]:
    """Every message in one of the user's conversations, oldest first."""
    conv = get_conversation(db, user, conversation_id)
    return list(db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id,
                                                     ChatMessage.conversation_id == conv.id)
                           .order_by(ChatMessage.created_at)).all())


def delete_conversation(db: Session, user: User, conversation_id: str) -> int:
    """Delete one of the user's conversations and its messages. Returns how many messages went."""
    conv = get_conversation(db, user, conversation_id)
    gone = db.execute(delete(ChatMessage).where(ChatMessage.user_id == user.id,
                                                ChatMessage.conversation_id == conv.id)).rowcount or 0
    db.delete(conv)
    audit(db, user.id, "chat.conversation_delete", f"chat_conversation:{conv.id}", {"messages": gone})
    db.commit()
    return gone


def extract_observations(db: Session, user: User, llm: LlmProvider, text: str, message_id: str,
                         audit_hook=None, now: datetime | None = None) -> list[Observation]:
    """Cheap-model extraction → validated, deduped (same label within 24 h) Observations."""
    try:
        data = llm.extract(extraction_system(), text, OBSERVATION_SCHEMA, model=llm.model_cheap,
                           tool_name="record_observations", purpose="chat_extraction", audit_hook=audit_hook)
    except LlmConfigError:
        raise  # misconfiguration (missing key) → 503 via app.main handler, not a silent fallback
    except LlmError as exc:
        log.warning("chat extraction failed: %s", exc)
        return []
    items = data.get("observations") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    now = now or datetime.now(timezone.utc)
    recent = set(db.scalars(select(Observation.label).where(
        Observation.user_id == user.id, Observation.created_at >= now - timedelta(hours=DEDUPE_HOURS))))
    created: list[Observation] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = it.get("kind")
        label = _snake(str(it.get("label") or ""))
        if kind not in EXTRACTABLE_KINDS or not label or label in recent:
            continue
        sev = it.get("severity")
        o = Observation(user_id=user.id, kind=kind, label=label, severity=sev if sev in SEVERITIES else None,
                        onset=(str(it["onset"])[:100] if it.get("onset") else None),
                        free_text=(str(it["free_text"])[:2000] if it.get("free_text") else None),
                        source="chat", source_id=message_id)
        db.add(o)
        created.append(o)
        recent.add(label)
    db.flush()
    return created


def assessment_hint(observations: list[Observation]) -> str | None:
    labels = {o.label for o in observations}
    return next((hint for hint, keys in _HINTS if labels & keys), None)


def handle_message(db: Session, user: User, text: str, llm: LlmProvider, embedding_provider: EmbeddingProvider | None,
                   language: str | None = None, audio_meta: dict[str, Any] | None = None, k: int = 6,
                   conversation_id: str | None = None, new_conversation: bool = False) -> ChatTurn:
    """Run one chat turn (see module docstring). Commits.

    ``conversation_id`` continues one of the user's conversations (``ConversationNotFound`` if it
    isn't theirs); ``new_conversation`` starts one titled from this message. With neither the turn
    joins the user's unthreaded messages (iOS Shortcut, older clients)."""
    text, lang, history, msg = _begin_turn(db, user, text, language, audio_meta, conversation_id, new_conversation)
    hook = llm_audit_hook(db, user.id, resource=f"chat_message:{msg.id}")
    context = build_context_block(db, user)
    pathway = rag_service.infer_pathway(text)
    hits = _retrieve(db, text, k, pathway, embedding_provider)
    result = rag_service.answer_with_citations(db, llm, text, hits, context, lang, history=history, audit_hook=hook)
    return _finish_turn(db, user, llm, msg, output_guard(result["answer"], lang), result, hits, pathway, lang,
                        audio_meta, hook)


def stream_message(db: Session, user: User, text: str, llm: LlmProvider,
                   embedding_provider: EmbeddingProvider | None, language: str | None = None, k: int = 6,
                   conversation_id: str | None = None, new_conversation: bool = False) -> Iterator[dict[str, Any]]:
    """`handle_message`, streamed. The message is checked and the conversation opened now, so
    ``ValueError`` / ``ConversationNotFound`` raise before any output. Returns the events:
    ``status`` (stage "searching", then "thinking"), ``thought`` (the model's thinking summaries),
    ``delta`` (answer text, already through the output guard's rewrites, a sentence at a time),
    ``answer`` (the final guarded answer + sources, sent before observation extraction) and
    ``done`` (``turn``: the ChatTurn). Commits."""
    text, lang, history, msg = _begin_turn(db, user, text, language, None, conversation_id, new_conversation)
    return _stream_turn(db, user, llm, embedding_provider, msg, text, lang, history, k)


def _stream_turn(db: Session, user: User, llm: LlmProvider, embedding_provider: EmbeddingProvider | None,
                 msg: ChatMessage, text: str, lang: str, history: list[dict[str, str]],
                 k: int) -> Iterator[dict[str, Any]]:
    yield {"type": "status", "stage": "searching"}
    hook = llm_audit_hook(db, user.id, resource=f"chat_message:{msg.id}")
    context = build_context_block(db, user)
    pathway = rag_service.infer_pathway(text)
    hits = _retrieve(db, text, k, pathway, embedding_provider)
    yield {"type": "status", "stage": "thinking"}
    guarded = _GuardedStream()
    result: dict[str, Any] = {}
    for kind, value in rag_service.stream_answer_with_citations(db, llm, text, hits, context, lang, history=history,
                                                                audit_hook=hook):
        if kind == "thought":
            yield {"type": "thought", "text": value}
        elif kind == "text":
            delta = guarded.feed(value)
            if delta:
                yield {"type": "delta", "text": delta}
        else:
            result = value
    answer = output_guard(result["answer"], lang)
    yield {"type": "answer", "answer": answer, "sources": result["sources"]}
    yield {"type": "done", "turn": _finish_turn(db, user, llm, msg, answer, result, hits, pathway, lang, None, hook)}


# Where streamed text can be cut: a sentence end (the guard's rewrites never cross one) or a line break.
_SAFE_CUT = re.compile(r"[.!?](?=\s)|[\n。！？]")


class _GuardedStream:
    """Turns raw streamed answer text into deltas that have been through `rewrite_phrasing`, a
    complete sentence at a time, so unguarded phrasing never reaches the client."""

    def __init__(self) -> None:
        self.raw = ""
        self.sent = ""

    def feed(self, chunk: str) -> str:
        self.raw += chunk
        text = self.raw.lstrip()
        cut = max((m.end() for m in _SAFE_CUT.finditer(text)), default=0)
        guarded = rewrite_phrasing(text[:cut])
        if not guarded.startswith(self.sent):  # a rewrite changed earlier text: the final answer event fixes it
            return ""
        delta, self.sent = guarded[len(self.sent):], guarded
        return delta


def _begin_turn(db: Session, user: User, text: str, language: str | None, audio_meta: dict[str, Any] | None,
                conversation_id: str | None,
                new_conversation: bool) -> tuple[str, str, list[dict[str, str]], ChatMessage]:
    """Validate the message, open the conversation and save the user's message (not committed)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty message")
    lang = resolve_language(text, language, user)
    conv = _open_conversation(db, user, conversation_id, text) if (conversation_id or new_conversation) else None
    history = _recent_history(db, user.id, conv.id if conv else None)
    if conv is not None:
        conv.updated_at = datetime.now(timezone.utc)

    msg = ChatMessage(user_id=user.id, conversation_id=conv.id if conv else None, role="user", content=text,
                      audio_url=(audio_meta or {}).get("audio_url"))
    db.add(msg)
    db.flush()
    return text, lang, history, msg


def _retrieve(db: Session, text: str, k: int, pathway: str | None,
              embedding_provider: EmbeddingProvider | None) -> list[Any]:
    try:
        return rag_service.retrieve(db, text, k=k, pathway=pathway, embedding_provider=embedding_provider)
    except Exception:  # embedding provider down → answer without excerpts
        log.exception("retrieval failed")
        return []


def _finish_turn(db: Session, user: User, llm: LlmProvider, msg: ChatMessage, answer: str, result: dict[str, Any],
                 hits: list[Any], pathway: str | None, lang: str, audio_meta: dict[str, Any] | None,
                 hook) -> ChatTurn:
    """Save the (guarded) answer, extract observations, audit and commit."""
    reply = ChatMessage(user_id=user.id, conversation_id=msg.conversation_id, role="assistant", content=answer,
                        sources=result["sources"])
    db.add(reply)
    db.flush()

    obs = extract_observations(db, user, llm, msg.content, msg.id, audit_hook=hook)
    audit(db, user.id, "chat.message", f"chat_message:{msg.id}",
          {"pathway": pathway, "n_hits": len(hits), "cited": result["cited"],
           "observations": [o.label for o in obs], "voice": bool(audio_meta)})
    db.commit()
    return ChatTurn(answer=answer, sources=result["sources"], cited=rag_service.cited_numbers(answer, len(hits)),
                    observations_created=obs, assessment_hint=assessment_hint(obs), message_id=reply.id, user_message_id=msg.id, language=lang,
                    conversation_id=msg.conversation_id)


def _speech_task(db: Session, user: User, transcript: Any, audio_bytes: bytes, audio_url: str | None,
                 lang: str) -> AssessmentTask | None:
    """AssessmentTask(free_speech) with lexical + temporal features; None if the speech module fails."""
    try:
        from app.ml.features.speech import lexical_features, temporal_features

        timing = transcript.word_timestamps or transcript.segments or []
        feats = {"lexical": lexical_features(transcript.text, lang),
                 "temporal": temporal_features(timing, transcript.duration_s, language=lang, text=transcript.text)}
    except Exception:
        log.exception("speech features failed")
        return None
    quality: dict[str, Any]
    valid, reason = bool(transcript.text.strip()), None
    try:
        from app.ml.features.quality import assess_quality, load_audio

        y, sr = load_audio(audio_bytes)
        q = assess_quality(y, sr)
        quality = q.to_dict()
        valid = valid and q.ok
        reason = "; ".join(q.reasons) or None
    except Exception:
        quality = {"assessed": False, "duration_s": transcript.duration_s, "reasons": ["audio_decode_failed"]}
    task = AssessmentTask(user_id=user.id, task_type="free_speech", protocol_version=FREE_SPEECH_PROTOCOL,
                          language=lang, recording_quality=quality, raw_url=audio_url,
                          raw_json={"transcript": transcript.to_dict()}, features=feats, valid=valid,
                          invalid_reason=None if valid else (reason or "empty_transcript"))
    db.add(task)
    db.flush()
    return task


def handle_voice_message(db: Session, user: User, audio_bytes: bytes, filename: str | None, stt: Any,
                         llm: LlmProvider, embedding_provider: EmbeddingProvider | None,
                         language: str | None = None, storage: Any = None,
                         conversation_id: str | None = None, new_conversation: bool = False) -> ChatTurn:
    """Transcribe (language forced) → store audio → AssessmentTask(free_speech) → :func:`handle_message`."""
    from app.providers.storage import get_storage

    lang = resolve_language("", language, user)  # explicit > profile language (forced for STT)
    transcript = stt.transcribe(audio_bytes, language=lang, filename=filename)
    text = (transcript.text or "").strip()
    if not text:
        raise ValueError("no speech recognised in the recording")
    audio_url = None
    try:
        audio_url = (storage or get_storage()).save(user.id, "chat_audio", filename or "voice.webm", audio_bytes)
    except Exception:
        log.exception("audio storage failed")
    task = _speech_task(db, user, transcript, audio_bytes, audio_url, lang)
    turn = handle_message(db, user, text, llm, embedding_provider, language=lang,
                          audio_meta={"audio_url": audio_url, "task_id": task.id if task else None},
                          conversation_id=conversation_id, new_conversation=new_conversation)
    turn.transcript = text
    turn.task_id = task.id if task else None
    return turn


def history(db: Session, user: User, limit: int = 50) -> list[ChatMessage]:
    """Last ``limit`` messages, oldest first."""
    rows = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id)
                      .order_by(ChatMessage.created_at.desc()).limit(limit)).all()
    return list(reversed(rows))
