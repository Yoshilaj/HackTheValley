"""Background transcription of uploaded audio documents (BACKEND_PLAN C1/C5).

``document_service.ingest_document`` stores audio uploads as
``UploadedDocument(kind='audio', status='pending_transcription')``. This job transcribes
them with the configured STT provider (spec §1.1 "Transcription": Whisper, language forced
to the user's language), writes ``extracted['transcript']`` (``Transcript.to_dict()``) and
sets ``status='transcribed'``; failures set ``status='failed'`` with ``extracted['error']``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import audit
from app.db.models import Profile, UploadedDocument
from app.jobs.registry import HOOK_PROCESS_PENDING_UPLOADS, job
from app.ml.features.speech import normalize_language
from app.providers.storage import Storage, get_storage
from app.providers.stt import SttProvider, get_stt_provider

log = get_logger(__name__)

PENDING = "pending_transcription"
TRANSCRIBED = "transcribed"
FAILED = "failed"
BATCH_LIMIT = 50


def _language_for(db: Session, doc: UploadedDocument) -> str:
    lang = (doc.extracted or {}).get("language")
    if not lang:
        prof = db.scalar(select(Profile).where(Profile.user_id == doc.user_id))
        lang = prof.language if prof else None
    return normalize_language(lang or "en")


def transcribe_document(db: Session, doc: UploadedDocument, stt: SttProvider,
                        storage: Storage | None = None) -> UploadedDocument:
    """Transcribe one audio document in place. Flushes; never raises (errors → status 'failed')."""
    storage = storage or get_storage()
    extracted = dict(doc.extracted or {})
    try:
        if not doc.file_url:
            raise ValueError("document has no stored file")
        data = storage.open(doc.file_url)
        lang = _language_for(db, doc)
        tr = stt.transcribe(data, lang, filename=extracted.get("filename"))
        extracted.pop("error", None)
        extracted["transcript"] = tr.to_dict()
        extracted["stt_provider"] = getattr(stt, "name", type(stt).__name__)
        doc.status = TRANSCRIBED
    except Exception as exc:  # noqa: BLE001 - recorded on the row
        log.warning("audio transcription failed", extra={"document_id": doc.id, "error": str(exc)})
        extracted["error"] = f"transcription failed: {exc}"[:500]
        doc.status = FAILED
    doc.extracted = extracted
    audit(db, doc.user_id, "document.transcribe", f"uploaded_document:{doc.id}", {"status": doc.status},
          actor="system")
    db.flush()
    return doc


def process_pending(db: Session, stt: SttProvider | None = None, storage: Storage | None = None,
                    limit: int = BATCH_LIMIT) -> dict[str, Any]:
    """Transcribe up to ``limit`` pending audio documents, oldest first."""
    docs = list(db.scalars(select(UploadedDocument).where(UploadedDocument.status == PENDING)
                           .order_by(UploadedDocument.created_at).limit(limit)))
    counts = {"pending": len(docs), TRANSCRIBED: 0, FAILED: 0}
    if not docs:
        return counts
    stt = stt or get_stt_provider()
    for doc in docs:
        transcribe_document(db, doc, stt, storage)
        counts[doc.status] = counts.get(doc.status, 0) + 1
        db.commit()
    return counts


@job(HOOK_PROCESS_PENDING_UPLOADS, interval_minutes=10)
def process_pending_uploads(db: Session) -> dict[str, Any]:
    """Scheduled every 10 min: transcribe ``pending_transcription`` audio uploads."""
    return process_pending(db)
