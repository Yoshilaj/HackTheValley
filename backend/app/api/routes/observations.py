"""Observations (symptoms, functional changes, medications, lifestyle, mood; A5 §2/§3/§6)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.security import audit
from app.db.models import OBSERVATION_KINDS, Observation, User
from app.db.session import get_db
from app.schemas.metrics import ObservationCreate, ObservationCreated, ObservationOut

router = APIRouter(prefix="/observations", tags=["observations"])


@router.get("", response_model=list[ObservationOut])
def list_observations(kind: str | None = Query(None, description="|".join(OBSERVATION_KINDS)),
                      days: int = Query(30, ge=1, le=3650), user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    q = select(Observation).where(Observation.user_id == user.id,
                                  Observation.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    if kind:
        if kind not in OBSERVATION_KINDS:
            raise HTTPException(422, f"kind must be one of {OBSERVATION_KINDS}")
        q = q.where(Observation.kind == kind)
    return db.scalars(q.order_by(Observation.created_at.desc())).all()


@router.post("", response_model=ObservationCreated, status_code=status.HTTP_201_CREATED)
def create_observation(req: ObservationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = Observation(user_id=user.id, **req.model_dump())
    db.add(row)
    db.flush()
    audit(db, user.id, "create", f"observation:{row.id}", {"kind": row.kind, "label": row.label})
    db.commit()
    return ObservationCreated.model_validate(row)


@router.delete("/{observation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_observation(observation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(Observation, observation_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Observation not found")
    db.delete(row)
    audit(db, user.id, "delete", f"observation:{observation_id}")
    db.commit()
