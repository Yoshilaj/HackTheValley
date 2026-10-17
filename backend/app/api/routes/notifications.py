"""User notification endpoints (thin; logic in app/services/notify.py)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.notifications import NotificationOut, ReadAllOut, UnreadCountOut
from app.services import notify as svc

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread: bool = False,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The current user's notifications, newest first."""
    return svc.list_notifications(db, user.id, unread_only=unread, limit=limit)


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return UnreadCountOut(unread=svc.unread_count(db, user.id))


@router.post("/read-all", response_model=ReadAllOut)
def read_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ReadAllOut(updated=svc.mark_all_read(db, user.id))


@router.post("/{notification_id}/read", response_model=NotificationOut)
def read_one(notification_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = svc.mark_read(db, user.id, notification_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    return row
