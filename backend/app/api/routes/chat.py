"""Chat (C2), check-in, RAG ingestion and research sources (C3). Thin: logic in services."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_admin_or_dev  # noqa: F401 (re-exported)
from app.core.ratelimit import rate_limit
from app.db.models import ResearchChunk, ResearchSource, User
from app.db.session import get_db
from app.providers.embeddings import EmbeddingProvider, get_embedding_provider
from app.providers.llm import LlmProvider, get_llm_provider
from app.providers.storage import Storage, get_storage
from app.providers.stt import SttProvider, get_stt_provider
from app.schemas.chat import (
    ChatMessageOut, ChatRequest, ChatResponse, CheckinQuestion, CheckinResult, CheckinSubmit, ConversationOut,
    IngestRequest, IngestResponse, ResearchSourceOut, VoiceChatResponse,
)
from app.services import chat_service, checkin_service

router = APIRouter(tags=["chat"])

MAX_AUDIO_BYTES = 25 * 1024 * 1024
AUDIO_TYPES = ("audio/", "video/webm", "application/octet-stream")


# ---- provider dependencies (tests override these with fakes)
def get_llm() -> LlmProvider:
    return get_llm_provider()


def get_embeddings() -> EmbeddingProvider:
    return get_embedding_provider()


def get_stt() -> SttProvider:
    return get_stt_provider()


def get_file_storage() -> Storage:
    return get_storage()


def _turn_response(turn: chat_service.ChatTurn) -> dict:
    return {"answer": turn.answer, "sources": turn.sources, "cited": turn.cited, "observations_created": turn.observations_created,
            "red_flags": turn.red_flags, "assessment_hint": turn.assessment_hint, "message_id": turn.message_id,
            "language": turn.language, "conversation_id": turn.conversation_id}


def _no_conversation() -> HTTPException:
    return HTTPException(404, "Conversation not found")


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(rate_limit("chat"))])
def chat(req: ChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db),
         llm: LlmProvider = Depends(get_llm), embeddings: EmbeddingProvider = Depends(get_embeddings)):
    """Advocate chat turn: red-flag short-circuit, RAG-cited answer, observation extraction."""
    try:
        turn = chat_service.handle_message(db, user, req.message, llm, embeddings, language=req.language,
                                           conversation_id=req.conversation_id,
                                           new_conversation=req.new_conversation)
    except chat_service.ConversationNotFound as exc:
        raise _no_conversation() from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _turn_response(turn)


@router.post("/chat/voice", response_model=VoiceChatResponse, dependencies=[Depends(rate_limit("chat"))])
async def chat_voice(file: UploadFile = File(...), language: str | None = Form(None),
                     conversation_id: str | None = Form(None), new_conversation: bool = Form(False),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db),
                     llm: LlmProvider = Depends(get_llm), embeddings: EmbeddingProvider = Depends(get_embeddings),
                     stt: SttProvider = Depends(get_stt), storage: Storage = Depends(get_file_storage)):
    """Voice turn: transcribe (language forced) → free_speech task → same pipeline as /chat."""
    ctype = (file.content_type or "").lower()
    if ctype and not ctype.startswith(AUDIO_TYPES):
        raise HTTPException(415, f"unsupported content type {ctype}")
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "audio too large (max 25 MB)")
    if not data:
        raise HTTPException(422, "empty audio")
    if language is not None and language not in ("ja", "en"):
        raise HTTPException(422, "language must be ja or en")
    try:
        turn = chat_service.handle_voice_message(db, user, data, file.filename, stt, llm, embeddings,
                                                 language=language, storage=storage,
                                                 conversation_id=conversation_id,
                                                 new_conversation=new_conversation)
    except chat_service.ConversationNotFound as exc:
        raise _no_conversation() from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**_turn_response(turn), "transcript": turn.transcript or "", "task_id": turn.task_id}


@router.get("/chat/conversations", response_model=list[ConversationOut])
def list_conversations(limit: int = Query(100, ge=1, le=500), user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Your conversations, most recently active first. Only ever the signed-in user's."""
    return chat_service.list_conversations(db, user, limit)


@router.get("/chat/conversations/{conversation_id}/messages", response_model=list[ChatMessageOut])
def conversation_messages(conversation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every message in one of your conversations, oldest first (404 for anyone else's)."""
    try:
        return chat_service.conversation_messages(db, user, conversation_id)
    except chat_service.ConversationNotFound as exc:
        raise _no_conversation() from exc


@router.delete("/chat/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete one of your conversations and its messages."""
    try:
        chat_service.delete_conversation(db, user, conversation_id)
    except chat_service.ConversationNotFound as exc:
        raise _no_conversation() from exc


@router.get("/chat/history", response_model=list[ChatMessageOut])
def chat_history(limit: int = Query(50, ge=1, le=500), user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return chat_service.history(db, user, limit)


@router.get("/chat/checkin", response_model=list[CheckinQuestion])
def get_checkin(language: str | None = Query(None, pattern="^(ja|en)$"), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    return checkin_service.daily_checkin_questions(db, user, language)


@router.post("/chat/checkin", response_model=CheckinResult)
def post_checkin(body: CheckinSubmit, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    res = checkin_service.submit_checkin(db, user, body.answers, on_date=body.date, language=body.language)
    return {"date": res["date"], "daily_log_id": res["daily_log"].id, "observations": res["observations"],
            "red_flags": res["red_flags"], "emergency_guidance": res["emergency_guidance"], "ignored": res["ignored"]}


@router.post("/ingest", response_model=IngestResponse, dependencies=[Depends(require_admin_or_dev)])
def ingest(req: IngestRequest, db: Session = Depends(get_db), embeddings: EmbeddingProvider = Depends(get_embeddings)):
    """Ad-hoc text ingestion into the research corpus (admin, or any caller in dev mode)."""
    from app.rag.ingest import ingest_text

    added = ingest_text(db, embeddings, req.text, req.source, title=req.title, pathway_tags=req.pathway_tags,
                        url=req.url, year=req.year)
    db.commit()
    return IngestResponse(chunks_added=added, source_id=req.source)


@router.post("/ingest/corpus", dependencies=[Depends(require_admin_or_dev)])
def ingest_corpus_route(force: bool = False, limit_chunks_per_source: int | None = Query(None, ge=1),
                        db: Session = Depends(get_db), embeddings: EmbeddingProvider = Depends(get_embeddings)):
    """(Re)ingest data/raw/research/sources.json (idempotent unless ``force``)."""
    from app.rag.ingest import DEFAULT_SOURCES_PATH, ingest_corpus

    return ingest_corpus(db, embeddings, DEFAULT_SOURCES_PATH, limit_chunks_per_source=limit_chunks_per_source,
                         force=force)


@router.get("/sources", response_model=list[ResearchSourceOut])
def list_sources(pathway: str | None = Query(None), db: Session = Depends(get_db)):
    """Research sources for the citation panel (public bibliographic data)."""
    counts = dict(db.execute(select(ResearchChunk.source_id, func.count(ResearchChunk.id))
                             .group_by(ResearchChunk.source_id)).all())
    rows = db.scalars(select(ResearchSource).order_by(ResearchSource.id)).all()
    out = []
    for r in rows:
        if pathway and pathway not in (r.pathway_tags or []):
            continue
        out.append({**ResearchSourceOut.model_validate(r).model_dump(), "chunk_count": counts.get(r.id, 0)})
    return out
