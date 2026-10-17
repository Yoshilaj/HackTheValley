"""Minimal idempotent demo seed.

    .venv/bin/python -m scripts.seed

Creates demo@example.com / demo1234 with a complete Canadian (Toronto, English) profile,
all consents granted, and prints a fresh device token (for the iOS Shortcut).
A richer planted-trend seeder can build on `seed_demo_user(db)`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.models import CONSENT_PURPOSES, Consent, Profile, User

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo1234"


def _dob_for_age(age: int) -> date:
    today = date.today()
    return date(today.year - age, 1, 15) if (today.month, today.day) >= (1, 15) else date(today.year - age - 1, 1, 15)


#: Canadian labs report cholesterol in mmol/L; Profile stores mg/dL (× 38.67, see app.ml.framingham).
DEMO_LABS_MMOL_L = {"ldl": 3.9, "hdl": 1.15, "total_chol": 5.8}


def _mg_dl(mmol_l: float) -> float:
    from app.ml.framingham import mmol_l_to_mg_dl

    return round(mmol_l_to_mg_dl(mmol_l), 1)


DEMO_PROFILE = dict(
    display_name="Robert MacLeod",
    first_name="Robert",   # legal name (fictional demo person)
    last_name="MacLeod",
    date_of_birth=None,  # filled at seed time -> age 68
    sex="male",
    height_cm=168.0,
    weight_kg=70.0,
    smoking_status="current",
    diabetes=False,
    ckd=False,
    egfr=70.0,
    ldl_mg_dl=_mg_dl(DEMO_LABS_MMOL_L["ldl"]),               # 150.8
    hdl_mg_dl=_mg_dl(DEMO_LABS_MMOL_L["hdl"]),               # 44.5
    total_chol_mg_dl=_mg_dl(DEMO_LABS_MMOL_L["total_chol"]),  # 224.3
    known_hypertension=False,
    known_af=False,
    known_chd=False,
    known_pd=False,
    known_dementia=False,
    family_history={"stroke": True, "dementia": False, "parkinsons": False, "chd": False, "premature_cvd": False},
    medications=[],
    address="100 Queen St W, Toronto, ON M5H 2N2",
    street="100 Queen St W",
    city="Toronto",
    postal_code="M5H 2N2",
    province="ON",
    phone="+1-416-555-0142",
    contact_email=None,  # clinics get the account e-mail (demo@example.com)
    family_doctor={"name": "Dr. Anita Shah", "clinic": "Kensington Family Health Team", "phone": "+1 416-555-0106"},
    has_referral=False,
    lat=43.6534,
    lng=-79.3841,
    language="en",
    timezone="America/Toronto",
    emergency_contact={"name": "Linda MacLeod", "relation": "spouse", "phone": "+1-416-555-0187",
                       "email": "linda@example.com"},
)


def seed_demo_user(db: Session) -> User:
    """Idempotent: returns the existing demo user with its profile refreshed IN PLACE (every
    DEMO_PROFILE field, incl. the onboarding identity fields, is overwritten) or creates it. Commits."""
    user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
    if user is None:
        user = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD))
        db.add(user)
        db.flush()
    profile = user.profile or Profile(user_id=user.id)
    for key, value in DEMO_PROFILE.items():
        setattr(profile, key, value)
    profile.date_of_birth = _dob_for_age(68)
    user.profile = profile

    active = set(
        db.scalars(select(Consent.purpose).where(Consent.user_id == user.id, Consent.revoked_at.is_(None)))
    )
    for purpose in CONSENT_PURPOSES:
        if purpose not in active:
            db.add(Consent(user_id=user.id, purpose=purpose))
    db.commit()
    db.refresh(user)
    return user


def main() -> None:
    from app.api.routes.auth import issue_device_token
    from app.db.session import SessionLocal, init_db

    init_db()
    with SessionLocal() as db:
        user = seed_demo_user(db)
        _, token = issue_device_token(db, user, "seed")
        db.commit()
        print(f"demo user: {DEMO_EMAIL} / {DEMO_PASSWORD}  id={user.id}")
        print(f"device token (X-Device-Token): {token}")


if __name__ == "__main__":
    main()
