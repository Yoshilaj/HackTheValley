from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.security import audit
from app.db.models import USER_OWNED_MODELS, Consent, OAuthCredential, Profile, User
from app.db.session import get_db
from app.providers.storage import Storage, get_storage
from app.schemas.profile import BookingReadinessOut, ConsentOut, ConsentRequest, ProfileOut, ProfileUpdate

router = APIRouter(tags=["profile"])


# ---------------------------------------------------------------- helpers

def get_or_create_profile(db: Session, user: User) -> Profile:
    if user.profile is None:
        user.profile = Profile()
        db.flush()
    return user.profile


def apply_profile_update(profile: Profile, update: ProfileUpdate) -> None:
    data = update.model_dump(exclude_unset=True)
    before = (profile.address, profile.postal_code)
    for field, value in data.items():
        if field in ("language", "timezone") and value is None:
            continue  # non-nullable with defaults
        setattr(profile, field, value)
    if ("street" in data or "city" in data) and "address" not in data and (profile.street or profile.city):
        # keep the free-form address (used for geocoding / clinic discovery) in sync with the parts
        profile.address = ", ".join(x for x in (profile.street, profile.city) if x)
    if (profile.address, profile.postal_code) != before and "lat" not in data and "lng" not in data:
        profile.lat = profile.lng = None  # moved: re-geocode at the next clinic discovery


def set_consent(db: Session, user_id: str, purpose: str, granted: bool) -> None:
    active = db.scalars(
        select(Consent).where(Consent.user_id == user_id, Consent.purpose == purpose, Consent.revoked_at.is_(None))
    ).all()
    if granted and not active:
        db.add(Consent(user_id=user_id, purpose=purpose))
    elif not granted:
        now = datetime.now(timezone.utc)
        for c in active:
            c.revoked_at = now
        if purpose == "share_with_clinic":
            # revoke → existing public report links stop working (review fix 5)
            from app.services.report_service import revoke_share_tokens

            db.flush()
            revoke_share_tokens(db, user_id)


def list_consents(db: Session, user_id: str) -> list[Consent]:
    return list(db.scalars(select(Consent).where(Consent.user_id == user_id).order_by(Consent.granted_at)))


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def row_to_dict(row) -> dict[str, Any]:
    return {c.key: _jsonable(getattr(row, c.key)) for c in row.__mapper__.column_attrs}


def export_user_data(db: Session, user: User) -> dict[str, Any]:
    data: dict[str, Any] = {"user": {k: v for k, v in row_to_dict(user).items() if k != "password_hash"}}
    for model in USER_OWNED_MODELS:
        rows = db.scalars(select(model).where(model.user_id == user.id)).all()
        items = [row_to_dict(r) for r in rows]
        if model is OAuthCredential:
            for item in items:
                item["encrypted_tokens"] = "<redacted>"
        data[model.__tablename__] = items
    return data


def delete_user_data(db: Session, user: User, storage: Storage | None = None) -> dict[str, int]:
    """Hard-delete every row owned by the user (OAuthCredential included; Google's revoke
    endpoint is not called), then every stored file of the user, then the user. Counts per table
    plus ``files`` (number of files removed). Caller commits."""
    counts: dict[str, int] = {}
    user_id = user.id
    # TODO(post-hackathon): call Google's token revoke endpoint (https://oauth2.googleapis.com/revoke)
    # for the user's OAuthCredential before deleting it, so the grant is also removed on Google's side.
    for model in USER_OWNED_MODELS:
        result = db.execute(delete(model).where(model.user_id == user_id))
        counts[model.__tablename__] = result.rowcount or 0
    # Files before the user row and in this same transaction: database storage deletes its rows
    # with this session (on its own connection it would wait on this transaction's locks).
    counts["files"] = (storage or get_storage()).delete_user_files(user_id, db=db)
    db.execute(delete(User).where(User.id == user_id))
    db.expunge_all()
    return counts


# ---------------------------------------------------------------- routes

@router.get("/profile", response_model=ProfileOut)
def read_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, user)
    db.commit()
    return profile


@router.put("/profile", response_model=ProfileOut)
def update_profile(req: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = get_or_create_profile(db, user)
    apply_profile_update(profile, req)
    audit(db, user.id, "update", "profile", {"fields": sorted(req.model_dump(exclude_unset=True))})
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/profile/booking-readiness", response_model=BookingReadinessOut)
def booking_readiness(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Which onboarding fields the booking agent still lacks (it cannot answer a clinic's question
    about them) and which fields clinics may receive. Health-card numbers are never collected."""
    from app.services.booking.common import booking_readiness as readiness

    get_or_create_profile(db, user)
    db.commit()
    return readiness(user)


@router.get("/consent", response_model=list[ConsentOut])
def get_consents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list_consents(db, user.id)


@router.post("/consent", response_model=list[ConsentOut])
def post_consent(req: ConsentRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    set_consent(db, user.id, req.purpose, req.granted)
    audit(db, user.id, "grant" if req.granted else "revoke", f"consent:{req.purpose}")
    db.commit()
    return list_consents(db, user.id)


@router.get("/me/export")
def export_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    data = export_user_data(db, user)
    audit(db, user.id, "export", "user", commit=True)
    return data


@router.delete("/me", status_code=status.HTTP_200_OK)
def delete_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    counts = delete_user_data(db, user)
    db.commit()
    return {"deleted": True, "counts": counts, "files_deleted": counts["files"]}
