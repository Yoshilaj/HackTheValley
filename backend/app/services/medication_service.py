"""Medications, refill requests and daily dose tick-offs.

The medication list is ``Profile.medications`` (a JSON list), because the risk engine (Framingham
"BP treated"), the chat context, reports and document extraction already read and extend it.
Entries written by those other paths may be minimal (``{"name", "dose", "schedule"}``); every entry
gets a stable ``id`` the first time it is listed, so refills and dose tick-offs can point at it.
Refill requests (``RefillRequest``) and ticked-off doses (``DoseLog``) are their own tables.
Everything is scoped to the signed-in user: another user's ids are "not found".
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import audit
from app.db.base import new_id
from app.db.models import DoseLog, Profile, RefillRequest, User

#: Fields a medication entry may carry (MedicationIn); anything else stored on an entry is kept.
FIELDS = ("name", "dose", "frequency", "purpose", "prescriber", "refills", "times", "days_left",
          "take_with", "side_effects", "interactions", "glyph")
LIST_FIELDS = ("times", "side_effects", "interactions")


class MedicationNotFound(LookupError):
    """No such medication on the user's list (the API answers 404)."""


def _profile(db: Session, user: User) -> Profile:
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if profile is None:
        profile = Profile(user_id=user.id)
        db.add(profile)
        db.flush()
    return profile


def _shape(entry: dict[str, Any]) -> dict[str, Any]:
    """An entry as the API returns it: every field present, lists as lists, legacy ``schedule`` as frequency."""
    out = {f: entry.get(f) for f in FIELDS}
    for f in LIST_FIELDS:
        out[f] = list(out[f] or [])
    if not out["frequency"] and entry.get("schedule"):
        out["frequency"] = entry["schedule"]
    return {"id": entry["id"], **out}


def _entries(db: Session, user: User) -> tuple[Profile, list[dict[str, Any]]]:
    """The profile and its medication entries, giving any entry without an id one (saved)."""
    profile = _profile(db, user)
    entries = [dict(m) if isinstance(m, dict) else {"name": str(m)} for m in (profile.medications or [])]
    if any(not e.get("id") for e in entries):
        for e in entries:
            if not e.get("id"):
                e["id"] = new_id()
        profile.medications = entries
        db.flush()
    return profile, entries


def _find(entries: list[dict[str, Any]], medication_id: str) -> dict[str, Any]:
    for e in entries:
        if e.get("id") == medication_id:
            return e
    raise MedicationNotFound(medication_id)


def list_medications(db: Session, user: User) -> list[dict[str, Any]]:
    _, entries = _entries(db, user)
    db.commit()
    return [_shape(e) for e in entries]


def add_medication(db: Session, user: User, fields: dict[str, Any]) -> dict[str, Any]:
    profile, entries = _entries(db, user)
    entry = {"id": new_id(), **{k: v for k, v in fields.items() if k in FIELDS}}
    profile.medications = [*entries, entry]
    audit(db, user.id, "create", f"medication:{entry['id']}", {"name": entry["name"]})
    db.commit()
    return _shape(entry)


def update_medication(db: Session, user: User, medication_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    profile, entries = _entries(db, user)
    entry = _find(entries, medication_id)
    entry.update({k: v for k, v in changes.items() if k in FIELDS})
    profile.medications = [dict(e) for e in entries]
    audit(db, user.id, "update", f"medication:{medication_id}", {"fields": sorted(changes)})
    db.commit()
    return _shape(entry)


def delete_medication(db: Session, user: User, medication_id: str) -> None:
    """Remove it from the list and withdraw any open refill request for it."""
    profile, entries = _entries(db, user)
    _find(entries, medication_id)
    profile.medications = [e for e in entries if e.get("id") != medication_id]
    _cancel_open(db, user, medication_id)
    audit(db, user.id, "delete", f"medication:{medication_id}")
    db.commit()


# ---------------------------------------------------------------- refills

def _open(db: Session, user: User, medication_id: str | None = None):
    q = select(RefillRequest).where(RefillRequest.user_id == user.id, RefillRequest.status == "requested")
    if medication_id is not None:
        q = q.where(RefillRequest.medication_id == medication_id)
    return q


def _cancel_open(db: Session, user: User, medication_id: str) -> int:
    rows = db.scalars(_open(db, user, medication_id)).all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.status, r.cancelled_at = "cancelled", now
    return len(rows)


def list_refills(db: Session, user: User) -> list[RefillRequest]:
    """The user's open refill requests, newest first."""
    return list(db.scalars(_open(db, user).order_by(RefillRequest.created_at.desc())).all())


def request_refills(db: Session, user: User, medication_ids: list[str], pharmacy_id: str | None,
                    method: str, note: str | None) -> list[RefillRequest]:
    """One open request per medication (one already open is kept, not duplicated). Returns the open
    requests for the medications asked about. Unknown medication ids -> MedicationNotFound."""
    _, entries = _entries(db, user)
    for mid in medication_ids:
        _find(entries, mid)
    already = {r.medication_id: r for r in db.scalars(_open(db, user)).all()}
    out = []
    for mid in dict.fromkeys(medication_ids):  # de-duplicate, keep order
        row = already.get(mid)
        if row is None:
            row = RefillRequest(user_id=user.id, medication_id=mid, pharmacy_id=pharmacy_id, method=method, note=note)
            db.add(row)
            db.flush()
            audit(db, user.id, "create", f"refill_request:{row.id}", {"medication_id": mid, "method": method})
        out.append(row)
    db.commit()
    return out


def cancel_refill(db: Session, user: User, medication_id: str) -> None:
    """Withdraw the open request for one medication (MedicationNotFound if there is none)."""
    if not _cancel_open(db, user, medication_id):
        raise MedicationNotFound(medication_id)
    audit(db, user.id, "cancel", f"refill_request:medication:{medication_id}")
    db.commit()


# ---------------------------------------------------------------- doses

def doses_for(db: Session, user: User, day: date) -> list[str]:
    return list(db.scalars(select(DoseLog.dose_key).where(DoseLog.user_id == user.id, DoseLog.day == day)
                           .order_by(DoseLog.created_at)).all())


def set_doses(db: Session, user: User, day: date, taken: list[str]) -> list[str]:
    """Replace the day's ticked-off doses with ``taken``."""
    keys = list(dict.fromkeys(k.strip() for k in taken if k and k.strip()))
    db.execute(delete(DoseLog).where(DoseLog.user_id == user.id, DoseLog.day == day))
    for k in keys:
        db.add(DoseLog(user_id=user.id, day=day, dose_key=k[:120]))
    db.commit()
    return keys
