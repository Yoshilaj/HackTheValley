"""Metrics, BP and device-rhythm endpoints (BACKEND_PLAN C1; spec §2, §4). Logic lives in
app/services/ingest_service.py."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.security import audit
from app.db.models import BpReading, DeviceRhythmResult, User
from app.db.session import get_db
from app.schemas.metrics import BpBatchIn, BpOut, MetricBatchIn, RhythmIn, RhythmOut, UpsertResultOut
from app.services import ingest_service as svc

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.post("", response_model=UpsertResultOut)
def post_metrics(req: MetricBatchIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Batch push (iOS Shortcut / app). Idempotent on (type, recorded_at, source)."""
    res = svc.upsert_metrics(db, user.id, req.samples, default_source=req.source)
    audit(db, user.id, "create", "metric_samples", {"inserted": res.inserted, "updated": res.updated,
                                                    "duplicates": res.duplicates, "rejected": len(res.rejected)})
    db.commit()
    return res.as_dict()


@router.get("/summary")
def get_summary(days: int = Query(30, ge=1, le=365), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    return svc.metrics_summary(db, user.id, days)


@router.post("/bp", response_model=list[BpOut], status_code=status.HTTP_201_CREATED)
def post_bp(req: BpBatchIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not req.readings:
        raise HTTPException(422, "readings must not be empty")
    rows = svc.add_bp_readings(db, user.id, req.readings)
    audit(db, user.id, "create", "bp_readings", {"ids": [r.id for r in rows]})
    db.commit()
    return rows


@router.get("/bp")
def get_bp(days: int = Query(14, ge=1, le=365), user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.scalars(select(BpReading).where(BpReading.user_id == user.id, BpReading.measured_at >= since)
                      .order_by(BpReading.measured_at.desc())).all()
    return {"readings": [BpOut.model_validate(r) for r in rows], "summary": svc.bp_summary(db, user.id, days)}


@router.post("/rhythm", response_model=RhythmOut, status_code=status.HTTP_201_CREATED)
def post_rhythm(req: RhythmIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Manual entry of a device rhythm result; the label is normalised, the raw string kept."""
    row = svc.add_rhythm_result(db, user.id, req.source, req.label, req.recorded_at, req.file_url, req.meta)
    audit(db, user.id, "create", f"device_rhythm_result:{row.id}", {"label": row.label})
    db.commit()
    return row


@router.get("/rhythm", response_model=list[RhythmOut])
def get_rhythm(days: int = Query(90, ge=1, le=3650), user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return db.scalars(select(DeviceRhythmResult).where(
        DeviceRhythmResult.user_id == user.id, DeviceRhythmResult.recorded_at >= since)
        .order_by(DeviceRhythmResult.recorded_at.desc())).all()
