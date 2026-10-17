"""Daily check-in log (A5 §3). True flags become Observations (spec §7, §1.3)."""

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


def _out(row: DailyLog, obs, guidance: str | None = None) -> DailyLogOut:
    out = DailyLogOut.model_validate(row)
    out.observations = [ObservationOut.model_validate(o) for o in obs]
    out.emergency_guidance = guidance
    return out


@router.post("/daily", response_model=DailyLogOut)
def post_daily(req: DailyLogIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.checkin_service import RED_FLAG_TO_LOG_FLAG
    from app.services.emergency import handle_red_flags
    from app.services.guardrails import detect_red_flags

    lang = (user.profile.language if user.profile else None) or "en"
    # Free-text red flags ("crushing chest pain") become red-flag flags → Observations (spec §7).
    detected = detect_red_flags(req.notes or "", lang)
    if detected:
        req.flags = {**(req.flags or {}), **{RED_FLAG_TO_LOG_FLAG[lbl]: True for lbl in detected}}
    row, obs = svc.add_daily_log(db, user.id, req)
    # labels raised by THIS request (flags sent now + detected text), not earlier same-day flags
    sent = {lbl for f, v in (req.flags or {}).items() if v
            for kind, lbl in svc.flag_to_observations(f) if kind == "red_flag"}
    red_obs = [o for o in obs if o.kind == "red_flag" and o.label in sent]
    guidance = handle_red_flags(db, user, [o.label for o in red_obs], source="log", source_id=row.id,
                                language=lang, observations=red_obs, extra={"daily_log_id": row.id})
    audit(db, user.id, "upsert", f"daily_log:{row.id}", {"date": req.date.isoformat(),
                                                          "observations": [o.label for o in obs],
                                                          "red_flags": [o.label for o in red_obs]})
    db.commit()
    return _out(row, obs, guidance)


@router.get("/daily", response_model=list[DailyLogOut])
def get_daily(days: int = Query(30, ge=1, le=365), user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=days)
    rows = db.scalars(select(DailyLog).where(DailyLog.user_id == user.id, DailyLog.date >= since)
                      .order_by(DailyLog.date.desc())).all()
    return [_out(r, svc.log_observations(db, user.id, r.id)) for r in rows]
