"""Home dashboard (BACKEND_PLAN C8; spec phase-a §11.4). Logic in app/services/dashboard_service.py."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.security import audit
from app.db.models import User
from app.db.session import get_db
from app.schemas.dashboard import DashboardOut
from app.services import dashboard_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(language: str | None = Query(None, pattern="^(ja|en)$"),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One call for the Home page: 4 pathway cards, metric summaries, pending appointments, notifications."""
    out = dashboard_service.dashboard(db, user, language)
    audit(db, user.id, "dashboard.read", f"user:{user.id}", commit=True)
    return out
