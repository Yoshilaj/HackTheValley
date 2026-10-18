"""Daily check-in log (A5 §3). True flags become Observations (spec §1.3)."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.security import audit
from app.db.models import DailyLog, User
from app.db.session import get_db
from app.schemas.metrics import DailyLogIn, DailyLogOut, ObservationOut
from app.services import ingest_service as svc

router = APIRouter(prefix="/logs", tags=["logs"])


def _out(row: DailyLog, obs) -> DailyLogOut:
    out = DailyLogOut.model_validate(row)
    out.observations = [ObservationOut.model_validate(o) for o in obs]
    return out


@router.post("/daily", response_model=DailyLogOut)
def post_daily(req: DailyLogIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row, obs = svc.add_daily_log(db, user.id, req)
    audit(db, user.id, "upsert", f"daily_log:{row.id}", {"date": req.date.isoformat(),
                                                          "observations": [o.label for o in obs]})
    db.commit()
    return _out(row, obs)


@router.get("/daily", response_model=list[DailyLogOut])
def get_daily(days: int = Query(30, ge=1, le=365), user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    rows = db.scalars(select(DailyLog).where(DailyLog.user_id == user.id, DailyLog.date >= since)
                      .order_by(DailyLog.date.desc())).all()
    return [_out(r, svc.log_observations(db, user.id, r.id)) for r in rows]
