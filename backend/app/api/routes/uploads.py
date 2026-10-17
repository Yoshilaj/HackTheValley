"""Uploads: Apple Health export (background job), documents and BP-monitor photos
(BACKEND_PLAN C1). Logic in app/services/{apple_health,document_service}.py."""

from __future__ import annotations

import mimetypes
import os
from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.ratelimit import rate_limit
from app.core.logging import get_logger
from app.core.security import audit
from app.db import session as db_session
from app.db.models import BpReading, Job, UploadedDocument, User
from app.db.session import get_db
from app.jobs.registry import STATUS_DONE, STATUS_FAILED, STATUS_PENDING, STATUS_RUNNING
from app.providers.llm import LlmProvider, get_llm_provider
from app.providers.storage import Storage, get_storage
from app.schemas.metrics import BpOut
from app.schemas.uploads import AppleHealthAccepted, BpPhotoOut, JobOut, UploadOut
from app.services.apple_health import import_apple_health
from app.services.document_service import DOCUMENT_KINDS, ingest_document

log = get_logger(__name__)
router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[Depends(rate_limit("uploads"))])

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_EXPORT_BYTES = 2 * 1024 * 1024 * 1024
ALLOWED_DOC_TYPES = ("application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp", "text/plain")
JOB_NAME = "apple_health_import"


class UploadTooLarge(Exception):
    pass


class _LimitedReader:
    """File-object wrapper that aborts the copy as soon as more than ``limit`` bytes were read
    (the size limit is enforced while streaming, not after writing the whole file)."""

    def __init__(self, fileobj, limit: int):
        self.fileobj, self.limit, self.total = fileobj, limit, 0

    def read(self, n: int = -1) -> bytes:
        chunk = self.fileobj.read(n)
        self.total += len(chunk)
        if self.total > self.limit:
            raise UploadTooLarge(f"upload exceeds {self.limit} bytes")
        return chunk


def get_llm() -> LlmProvider:
    """Dependency (override in tests)."""
    return get_llm_provider()


def get_storage_dep() -> Storage:
    return get_storage()


def _media_type(file: UploadFile) -> str:
    mt = (file.content_type or "").split(";")[0].strip().lower()
    if not mt or mt == "application/octet-stream":
        mt = mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    return mt


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"file larger than {limit // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, "empty file")
    return data


# ---------------------------------------------------------------- Apple Health

def run_apple_health_import(job_id: str, user_id: str, upload_id: str, file_url: str, since: date | None) -> None:
    """Background task: own DB session; records progress in Job + UploadedDocument."""
    db = db_session.SessionLocal()
    try:
        job = db.get(Job, job_id)
        doc = db.get(UploadedDocument, upload_id)
        job.status, job.started_at = STATUS_RUNNING, datetime.now(timezone.utc)
        doc.status = "processing"
        db.commit()
        try:
            storage = get_storage()
            result = import_apple_health(db, user_id, storage.local_path(file_url), storage=storage, since=since)
            job.status = STATUS_DONE
            job.result = {**(job.result or {}), **result}
            doc.status = "done"
            doc.extracted = {**(doc.extracted or {}), "result": result}
            audit(db, user_id, "import", "apple_health", {"job_id": job_id, **result["metrics"]}, actor="system")
        except Exception as e:  # noqa: BLE001 - recorded on the job
            log.exception("apple health import failed")
            db.rollback()
            job = db.get(Job, job_id)
            doc = db.get(UploadedDocument, upload_id)
            job.status, job.error = STATUS_FAILED, f"{type(e).__name__}: {e}"
            doc.status = "failed"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


@router.post("/apple-health", response_model=AppleHealthAccepted, status_code=status.HTTP_202_ACCEPTED)
def upload_apple_health(background: BackgroundTasks, file: UploadFile = File(...),
                        since: date | None = Form(None), user: User = Depends(get_current_user),
                        db: Session = Depends(get_db), storage: Storage = Depends(get_storage_dep)):
    """Upload `export.zip` (or `export.xml`); parsing runs in the background. Poll
    `GET /api/uploads/jobs/{job_id}`."""
    try:
        file_url = storage.save_stream(user.id, "apple_health", file.filename or "export.zip",
                                       _LimitedReader(file.file, MAX_EXPORT_BYTES))
    except UploadTooLarge:
        raise HTTPException(422, "empty or too large export") from None
    size = os.path.getsize(file_url)
    if size == 0 or size > MAX_EXPORT_BYTES:
        os.remove(file_url)
        raise HTTPException(422, "empty or too large export")
    doc = UploadedDocument(user_id=user.id, kind="apple_health", file_url=file_url, status="pending",
                           extracted={"filename": file.filename, "bytes": size})
    db.add(doc)
    db.flush()
    job = Job(name=JOB_NAME, status=STATUS_PENDING, result={"user_id": user.id, "upload_id": doc.id})
    db.add(job)
    db.flush()
    audit(db, user.id, "upload", f"uploaded_document:{doc.id}", {"kind": "apple_health", "job_id": job.id})
    db.commit()
    background.add_task(run_apple_health_import, job.id, user.id, doc.id, file_url, since)
    return AppleHealthAccepted(job_id=job.id, upload_id=doc.id)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None or (job.result or {}).get("user_id") != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


# ---------------------------------------------------------------- documents

@router.post("/document", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def upload_document(file: UploadFile = File(...), kind: str = Form(..., description="|".join(DOCUMENT_KINDS)),
                          user: User = Depends(get_current_user), db: Session = Depends(get_db),
                          llm: LlmProvider = Depends(get_llm), storage: Storage = Depends(get_storage_dep)):
    """Doctor note / prescription / lab (PDF, image, text), ECG PDF, BP photo, or audio."""
    if kind not in DOCUMENT_KINDS:
        raise HTTPException(422, f"kind must be one of {DOCUMENT_KINDS}")
    mt = _media_type(file)
    if not (mt in ALLOWED_DOC_TYPES or mt.startswith("audio/")):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f"unsupported type {mt}; use PDF, JPEG, PNG, GIF, WebP, text or audio")
    data = await _read_limited(file, MAX_DOCUMENT_BYTES)
    doc = ingest_document(db, user.id, kind, file.filename or "upload", data, mt, llm, storage)
    audit(db, user.id, "upload", f"uploaded_document:{doc.id}", {"kind": kind, "status": doc.status})
    db.commit()
    return doc


@router.post("/bp-photo", response_model=BpPhotoOut, status_code=status.HTTP_201_CREATED)
async def upload_bp_photo(file: UploadFile = File(...), user: User = Depends(get_current_user),
                          db: Session = Depends(get_db), llm: LlmProvider = Depends(get_llm),
                          storage: Storage = Depends(get_storage_dep)):
    """Photo of a BP monitor display → Claude vision → BpReading (source 'omron_photo')."""
    mt = _media_type(file)
    if not mt.startswith("image/"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "upload a JPEG, PNG, GIF or WebP photo")
    data = await _read_limited(file, MAX_DOCUMENT_BYTES)
    doc = ingest_document(db, user.id, "bp_photo", file.filename or "bp.jpg", data, mt, llm, storage)
    audit(db, user.id, "upload", f"uploaded_document:{doc.id}", {"kind": "bp_photo", "status": doc.status})
    db.commit()
    ids = ((doc.extracted or {}).get("created") or {}).get("bp_reading_ids") or []
    rows = db.scalars(select(BpReading).where(BpReading.id.in_(ids))).all() if ids else []
    return BpPhotoOut(document=UploadOut.model_validate(doc), bp_readings=[BpOut.model_validate(r) for r in rows])


@router.get("", response_model=list[UploadOut])
def list_uploads(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(UploadedDocument).where(UploadedDocument.user_id == user.id)
                      .order_by(UploadedDocument.created_at.desc())).all()
