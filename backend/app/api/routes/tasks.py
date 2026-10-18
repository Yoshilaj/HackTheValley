"""Assessment-task endpoints (BACKEND_PLAN C2 task prompts; spec phase-a-assessments §1, §1.4,
§5.3, §5.4, §10). Thin: logic lives in ``app/services/task_service.py``.

Provider dependencies (override in tests via ``app.dependency_overrides``):
``get_stt``, ``get_tts``, ``get_embeddings``, ``get_task_storage``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import get_current_user
from app.core.ratelimit import rate_limit
from app.db.models import User
from app.db.session import get_db
from app.providers.embeddings import EmbeddingProvider, get_embedding_provider
from app.providers.storage import Storage, get_storage
from app.providers.stt import SttProvider, get_stt_provider
from app.providers.tts import TtsProvider, get_tts_provider
from app.schemas.tasks import (
    StoryStatusOut, TappingIn, TappingOut, TaskHistoryOut, VowelOut, WordRecallOut, WordRecallStartIn,
    WordRecallStartOut, WordRecallSubmitIn,
)
from app.services import task_service as svc

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(rate_limit("tasks"))])

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # Whisper API limit
# The audio endpoints are async only to read the upload; decoding, speech-to-text and the voice
# features run in a worker thread so one recording doesn't stall every other request on the
# single uvicorn worker (a vowel takes ~30 s on the free instance).


def get_stt() -> SttProvider:
    """Dependency (override in tests)."""
    return get_stt_provider()


def get_tts() -> TtsProvider:
    """Dependency (override in tests)."""
    return get_tts_provider()


def get_embeddings() -> EmbeddingProvider:
    """Dependency (override in tests)."""
    return get_embedding_provider()


def get_task_storage() -> Storage:
    """Dependency (override in tests)."""
    return get_storage()


def _raise(e: svc.TaskError) -> JSONResponse:
    return JSONResponse(status_code=e.status_code, content={"detail": e.detail, **e.extra})


async def _read_audio(file: UploadFile | None) -> bytes | None:
    if file is None:
        return None
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "audio larger than 25 MB")
    return data or None


# ---------------------------------------------------------------- story recall (§1)

@router.post("/story/start", response_model=StoryStatusOut, status_code=201)
def story_start(request: Request, language: str | None = Query(default=None, pattern="^(en|ja)(-[A-Za-z]+)?$"),
                device: str | None = Query(default=None, max_length=64),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Any:
    try:
        task, session = svc.start_story_session(db, user, language,
                                                device=svc.device_label(request.headers.get("user-agent"), device))
    except svc.TaskError as e:
        return _raise(e)
    return svc.story_status(task, session)


@router.get("/story/{task_id}", response_model=StoryStatusOut)
def story_get(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Any:
    try:
        task, session = svc.get_story_task(db, user, task_id)
    except svc.TaskError as e:
        return _raise(e)
    return svc.story_status(task, session)


@router.get("/story/{task_id}/audio/{step}", response_class=Response,
            responses={200: {"content": {"audio/mpeg": {}, "audio/wav": {}}}})
def story_audio(task_id: str, step: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
                tts: TtsProvider = Depends(get_tts), storage: Storage = Depends(get_task_storage)) -> Response:
    try:
        _, session = svc.get_story_task(db, user, task_id)
        data, media_type = svc.get_story_audio(session, step, tts, storage)
    except svc.TaskError as e:
        return _raise(e)
    return Response(content=data, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/story/{task_id}/step", response_model=StoryStatusOut)
async def story_step(
    request: Request,
    task_id: str,
    step: str = Form(...),
    audio: UploadFile | None = File(default=None),
    transcript: str | None = Form(default=None, max_length=20000),
    force_delay: bool = Form(default=False),
    played_s: float | None = Form(default=None, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    stt: SttProvider = Depends(get_stt),
    embeddings: EmbeddingProvider = Depends(get_embeddings),
    storage: Storage = Depends(get_task_storage),
) -> Any:
    data = await _read_audio(audio)
    try:
        task, session = await run_in_threadpool(
            svc.submit_step, db, user, task_id, step, data, transcript, stt, embeddings=embeddings, storage=storage,
            force_delay=force_delay, played_s=played_s, filename=audio.filename if audio else None,
            device=svc.device_label(request.headers.get("user-agent")),
        )
    except svc.TaskError as e:
        return _raise(e)
    return svc.story_status(task, session)


# ---------------------------------------------------------------- sustained vowel (§5.4)

@router.post("/vowel", response_model=VowelOut, status_code=201)
async def vowel(
    request: Request,
    audio: UploadFile = File(...),
    repetition: int = Form(default=1, ge=1, le=10),
    device: str | None = Form(default=None, max_length=64),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: Storage = Depends(get_task_storage),
) -> Any:
    data = await _read_audio(audio)
    if not data:
        raise HTTPException(422, "empty audio file")
    try:
        return await run_in_threadpool(svc.submit_vowel, db, user, data, audio.filename, repetition, storage,
                                       device=svc.device_label(request.headers.get("user-agent"), device))
    except svc.TaskError as e:
        return _raise(e)


# ---------------------------------------------------------------- finger tapping (§5.3)

@router.post("/tapping", response_model=TappingOut, status_code=201)
def tapping(body: TappingIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Any:
    """Keyboard BRAIN tap test (§5.3). Keyboard only: pointer / touch input is refused with 422."""
    if body.input_method.strip().lower() != "keyboard":
        raise HTTPException(422, "keyboard tapping only")
    task = svc.submit_tapping(db, user, [e.model_dump() for e in body.left.events],
                              [e.model_dump() for e in body.right.events], body.duration_s, keys=body.keys)
    return {"task_id": task.id, "valid": task.valid, "reason": task.invalid_reason, "features": task.features,
            "message": svc.tapping_message(task)}


# ---------------------------------------------------------------- 3-word recall (§1.4)

@router.post("/word-recall/start", response_model=WordRecallStartOut, status_code=201)
def word_recall_start(body: WordRecallStartIn | None = None, language: str | None = Query(default=None),
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Any:
    task = svc.start_word_recall(db, user, (body.language if body else None) or language)
    raw = task.raw_json or {}
    return {"task_id": task.id, "set_id": raw["set_id"], "language": task.language, "words": raw["words"],
            "instruction": raw["instruction"], "recall_prompt": raw["recall_prompt"],
            "presented_at": raw["presented_at"]}


@router.post("/word-recall/{task_id}", response_model=WordRecallOut)
def word_recall_submit(task_id: str, body: WordRecallSubmitIn, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> Any:
    try:
        task = svc.submit_word_recall(db, user, task_id, body.response_text, body.latency_ms)
    except svc.TaskError as e:
        return _raise(e)
    f = task.features or {}
    return {"task_id": task.id, **{k: f.get(k) for k in ("score", "max_score", "recalled", "missed", "latency_ms",
                                                         "valid")}}


# ---------------------------------------------------------------- history

@router.get("/history", response_model=TaskHistoryOut)
def history(task_type: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Any:
    try:
        return {"items": svc.list_tasks(db, user, task_type, limit)}
    except svc.TaskError as e:
        return _raise(e)
