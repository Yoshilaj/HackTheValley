"""Observations (symptoms, functional changes, medications, red flags…; A5 §2/§3/§6)."""

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
    from app.services.emergency import handle_red_flags
    from app.services.guardrails import RED_FLAG_LABELS, detect_red_flags

    row = Observation(user_id=user.id, **req.model_dump())
    db.add(row)
    db.flush()
    audit(db, user.id, "create", f"observation:{row.id}", {"kind": row.kind, "label": row.label})
    # Red flags from user input take the shared emergency path (spec §7).
    lang = (user.profile.language if user.profile else None) or "en"
    red_obs: list[Observation] = []
    if row.kind == "red_flag" or row.label in RED_FLAG_LABELS:
        if row.kind == "red_flag" and not row.severity:
            row.severity = "urgent"
        red_obs.append(row)
    for lbl in detect_red_flags(f"{row.label}. {row.free_text or ''}", lang):
        if any(o.label == lbl for o in red_obs):
            continue
        extra = Observation(user_id=user.id, kind="red_flag", label=lbl, severity="urgent", onset=row.onset,
                            free_text=(row.free_text or "")[:500], source=row.source, source_id=row.id)
        db.add(extra)
        red_obs.append(extra)
    db.flush()
    guidance = handle_red_flags(db, user, [o.label for o in red_obs], source="observation", source_id=row.id,
                                language=lang, observations=red_obs)
    db.commit()
    out = ObservationCreated.model_validate(row)
    out.emergency_guidance = guidance
    return out


@router.delete("/{observation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_observation(observation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(Observation, observation_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Observation not found")
    db.delete(row)
    audit(db, user.id, "delete", f"observation:{observation_id}")
    db.commit()
