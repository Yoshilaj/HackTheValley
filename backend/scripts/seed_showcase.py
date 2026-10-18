"""Showcase account: every screen filled in, overall risk red, three conditions marked differently.

    .venv/bin/python -m scripts.seed_showcase

Creates showcase@example.com / showcase1234 (Margaret Kowalski, 71, Toronto) with:
* the planted 35-day trend from ``scripts.seed_demo`` (vitals, home BP, AFib result, tapping, voice,
  story recall, fatigue logs), with a wider morning/evening BP gap so BP-04 also fires;
* three medications;
* one assessment run (no follow-up hooks, no e-mail), giving
  cardio → followup_recommended [BP-02, BP-04, RHY-01] score 80 (overall card red, below the 85 hold),
  parkinsons → watch [PD-04, PD-03B], cognitive → no_finding;
* a confirmed cardiology appointment three days out, linked to the cardio assessment.

Idempotent: reruns refresh the profile and skip the data, medications and appointment already there.
Production: ``POST /api/admin/seed-showcase`` (X-Admin-Secret) runs the same ``seed_showcase``.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.models import CONSENT_PURPOSES, Appointment, BpReading, Consent, Profile, User
from app.services.context_builder import user_zone
from scripts.seed import DEMO_PROFILE, _dob_for_age
from scripts.seed_demo import seed_planted_trend
from scripts.seed_demo_medications import MEDICATIONS

EMAIL = "showcase@example.com"
PASSWORD = "showcase1234"
PROFILE = DEMO_PROFILE | dict(
    display_name="Margaret Kowalski", first_name="Margaret", last_name="Kowalski", sex="female",
    height_cm=162.0, weight_kg=68.0, smoking_status="former",
    address="250 Front St W, Toronto, ON M5V 3G5", street="250 Front St W", postal_code="M5V 3G5",
    phone="+1-416-555-0173", lat=43.6440, lng=-79.3866,
    family_history={"stroke": True, "dementia": True, "parkinsons": False, "chd": True, "premature_cvd": False},
    allergies=["Penicillin"], conditions=["Prediabetes"],
    emergency_contact={"name": "Daniel Kowalski", "relation": "son", "phone": "+1-416-555-0191",
                       "email": "daniel@example.com"},
)
#: added to the planted morning / evening systolic over the last 7 days: morning − evening ≈ 17 mmHg → BP-04
MORNING_SYS_SHIFT, EVENING_SYS_SHIFT = 6, -5
CLINIC = {"id": "seed_bloor_heart", "name": "Bloor Street Heart Clinic", "specialty": "cardiology",
          "kind": "specialist", "address": "401 Bloor St W, Toronto, ON M5S 1X5 (fictional)", "distance_km": 3.1,
          "phone": "+1 416-555-0101", "email": "booking@bloorheart.example.com",
          "timezone": "America/Toronto", "source": "seed"}


def seed_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == EMAIL))
    if user is None:
        user = User(email=EMAIL, password_hash=hash_password(PASSWORD))
        db.add(user)
        db.flush()
    profile = user.profile or Profile(user_id=user.id)
    for key, value in PROFILE.items():
        setattr(profile, key, value)
    profile.date_of_birth = _dob_for_age(71)
    user.profile = profile
    active = set(db.scalars(select(Consent.purpose).where(Consent.user_id == user.id, Consent.revoked_at.is_(None))))
    db.add_all(Consent(user_id=user.id, purpose=p) for p in CONSENT_PURPOSES if p not in active)
    db.commit()
    db.refresh(user)
    return user


def widen_bp_gap(db: Session, user: User, now: datetime) -> None:
    for r in db.scalars(select(BpReading).where(BpReading.user_id == user.id,
                                                BpReading.measured_at >= now - timedelta(days=7))):
        r.systolic += MORNING_SYS_SHIFT if r.session == "morning" else EVENING_SYS_SHIFT
    db.commit()


def seed_appointment(db: Session, user: User, cardio_assessment_id: str, now: datetime) -> Appointment:
    existing = db.scalar(select(Appointment).where(Appointment.user_id == user.id, Appointment.status == "confirmed"))
    if existing is not None:
        return existing
    tz = user_zone(user.profile)
    start = datetime.combine(now.astimezone(tz).date() + timedelta(days=3), time(10, 0), tzinfo=tz)
    slot = {"start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat()}
    appt = Appointment(
        user_id=user.id, assessment_id=cardio_assessment_id, clinic=CLINIC, proposed_slots=[slot], chosen_slot=slot,
        status="confirmed", channel="email", candidates=[CLINIC],
        channel_attempts=[{"channel": "email", "status": "confirmed", "clinic_id": CLINIC["id"],
                           "ts": (now - timedelta(hours=20)).isoformat()}],
        approval={"status": "approved", "requested_at": (now - timedelta(days=1)).isoformat(),
                  "approved_at": (now - timedelta(hours=23)).isoformat(),
                  "clinic": {"id": CLINIC["id"], "name": CLINIC["name"], "distance_km": CLINIC["distance_km"]}},
        agent_log=[{"ts": (now - timedelta(hours=20)).isoformat(), "tool": "seed", "input": {},
                    "output": "Clinic confirmed by e-mail (seeded)"}],
    )
    db.add(appt)
    db.commit()
    return appt


def seed_showcase(db: Session, now: datetime | None = None) -> dict:
    """Seed (or top up) the showcase account → ``{user_id, email, seed, assessments, appointment}``. Commits."""
    from unittest import mock

    from app.services import medication_service
    from app.services import notify as notify_svc
    from app.services.risk_service import assess_user, latest_assessments

    now = now or datetime.now(timezone.utc)
    user = seed_user(db)
    planted = seed_planted_trend(db, user, now=now)
    if not planted.get("skipped") and not planted.get("upgraded"):
        widen_bp_gap(db, user, now)
    if not medication_service.list_medications(db, user):
        for med in MEDICATIONS:
            medication_service.add_medication(db, user, med)
    rows = latest_assessments(db, user.id)
    if not planted.get("skipped") or "cardio" not in rows:
        # No e-mails from the seeded assessment (follow-up hooks are not registered either).
        with mock.patch.object(notify_svc, "_send", lambda *a, **k: {"accepted": False, "reason": "seed_showcase"}):
            rows = {r.pathway: r for r in assess_user(db, user, trigger="manual")}
    appt = seed_appointment(db, user, rows["cardio"].id, now)
    return {"user_id": user.id, "email": EMAIL, "seed": planted,
            "assessments": [{"pathway": p, "level": r.level, "priority_score": r.priority_score,
                             "fired": [f["id"] for f in r.fired_rules or []]} for p, r in rows.items()],
            "appointment": {"status": appt.status, "clinic": CLINIC["name"], "start": appt.chosen_slot["start"]}}


def main() -> None:
    from app.db.session import SessionLocal, init_db

    init_db()
    with SessionLocal() as db:
        out = seed_showcase(db)
    print(f"{EMAIL} / {PASSWORD}  id={out['user_id']}")
    for a in out["assessments"]:
        print(f"  {a['pathway']:<11} {a['level']:<22} score={a['priority_score']} fired={a['fired']}")
    print(f"  appointment: {out['appointment']}")

if __name__ == "__main__":
    main()
