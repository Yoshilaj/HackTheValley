"""Parkinson's screener (spec phase-a §5.1). Scoring via app/ml/screener.py (PD-01)."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.content.loader import get_screener
from app.core.auth import get_current_user
from app.core.security import audit
from app.db.models import ScreenerResponse, User
from app.db.session import get_db
from app.ml.screener import PD_SCREENER_ITEMS, score_pd_screener
from app.schemas.risk import ScreenerAnswersIn, ScreenerOut, ScreenerResultOut
from app.services.context_builder import normalize_screener_answers
from app.services.dashboard_service import normalize_language

router = APIRouter(prefix="/screener", tags=["screener"])

PD_NEXT_TASKS = ["sustained_vowel", "finger_tap"]
_ENGINE_IDS = {i.id for i in PD_SCREENER_ITEMS}


@router.get("/pd", response_model=ScreenerOut)
def get_pd(language: str | None = Query(None, pattern="^(ja|en)$"), user: User = Depends(get_current_user)):
    lang = normalize_language(language, user)
    s = get_screener("pd")
    return ScreenerOut(id=s.id, version=s.version, language=lang, period=s.period.get(lang, ""),
                       items=s.for_language(lang))


@router.post("/pd", response_model=ScreenerResultOut, status_code=status.HTTP_201_CREATED)
def post_pd(body: ScreenerAnswersIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Store answers (content-bank item ids; legacy ids accepted) with score = number of 'yes'; ``next_tasks`` when PD-01 would fire."""
    answers = normalize_screener_answers(body.answers)
    unknown = sorted(k for k in answers if k not in _ENGINE_IDS)
    if unknown:
        raise HTTPException(422, f"Unknown screener items: {unknown}")
    motor, nonmotor, fired = score_pd_screener(answers)
    row = ScreenerResponse(user_id=user.id, screener="pd", answers=answers, score=float(motor + nonmotor))
    db.add(row)
    db.flush()
    audit(db, user.id, "screener.submit", f"screener_response:{row.id}", {"screener": "pd", "pd01": fired})
    db.commit()
    return ScreenerResultOut(id=row.id, screener="pd", score=row.score, motor_count=motor, nonmotor_count=nonmotor,
                             pd01_would_fire=fired, next_tasks=PD_NEXT_TASKS if fired else [],
                             created_at=row.created_at)
