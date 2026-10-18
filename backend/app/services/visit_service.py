"""Clinic directory, follow-up recommendations and visits the user books themselves.

* ``list_providers``  - clinics near the user (Google Places when configured, else the fictional
  seed list), each with open times worked out from its opening hours.
* ``recommendations`` - one clinic per pathway that has a finding (cardio -> cardiology, ...), with
  the reason taken from the latest assessment; a family-medicine check-up when nothing is flagged.
* ``book`` / ``list_bookings`` / ``cancel_booking`` - ``VisitBooking`` rows (the clinic is not
  contacted; the booking agent's appointments are a separate flow).

Times are local ISO strings without an offset ("2026-10-21T10:00"), in the clinic's time zone.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import audit
from app.db.models import Profile, User, VisitBooking
from app.integrations import Clinic
from app.services import clinics as clinic_directory

SPECIALTY_LABELS = {
    "cardiology": "Cardiology", "neurology": "Neurology", "memory_clinic": "Memory clinic",
    "geriatrics": "Geriatrics", "family_medicine": "Family medicine", "walk_in": "Walk-in clinic",
    "hospital": "Hospital",
}
KIND_TYPES = {"hospital": "hospital"}  # everything else shows as a clinic
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FLAGGED = {"watch", "followup_recommended", "emergency"}

SLOT_STEP = timedelta(minutes=90)  # spread the offered times through each day
SLOTS_PER_CLINIC = 6
SLOT_DAYS = 10
FIRST_SLOT, LAST_SLOT_END = time(8, 0), time(20, 0)

#: The directory (no specialty asked): where people go first in Canada, then the specialists the
#: risk pathways refer to. One Places search each (cached), merged and sorted by distance.
DIRECTORY_SPECIALTIES = ("family_medicine", "walk_in", "hospital", "cardiology", "neurology", "memory_clinic")
DIRECTORY_LIMIT = 40


class BookingNotFound(LookupError):
    """No such booking for this user (the API answers 404)."""


class ProviderNotFound(LookupError):
    """The provider id isn't in the directory and no details were sent with the booking."""


# ---------------------------------------------------------------- directory

def _at(day, hhmm: str) -> datetime:
    """``day`` at "HH:MM"; Google gives "24:00" for midnight at the end of the day."""
    if hhmm == "24:00":
        return datetime.combine(day + timedelta(days=1), time(0))
    return datetime.combine(day, time.fromisoformat(hhmm))


def open_slots(clinic: Clinic, now: datetime | None = None, count: int = SLOTS_PER_CLINIC,
               days: int = SLOT_DAYS) -> list[str]:
    """The next ``count`` bookable times inside the clinic's opening hours, starting tomorrow."""
    zone = ZoneInfo(clinic.timezone or clinic_directory.DEFAULT_TZ)
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    hours = clinic.opening_hours or {}
    out: list[str] = []
    for offset in range(1, days + 1):
        day = (now + timedelta(days=offset)).date()
        for opens, closes in hours.get(WEEKDAYS[day.weekday()], []):
            # Appointments only in office hours, also at places open late or around the clock.
            start = max(_at(day, opens), datetime.combine(day, FIRST_SLOT))
            end = min(_at(day, closes), datetime.combine(day, LAST_SLOT_END)) - timedelta(minutes=30)
            t = start + timedelta(minutes=30)  # not the first minute the doors open
            while t <= end and len(out) < count:
                out.append(t.strftime("%Y-%m-%dT%H:%M"))
                t += SLOT_STEP
        if len(out) >= count:
            break
    return out


def slot_label(slot: str | None) -> str:
    """"Tue, Oct 21 · 10:00 AM" (or "Call to book" without a time)."""
    if not slot:
        return "Call to book"
    d = datetime.fromisoformat(slot)
    return f"{d.strftime('%a, %b')} {d.day} · {d.strftime('%I:%M %p').lstrip('0')}"


def hours_summary(opening_hours: dict[str, list[list[str]]] | None) -> str | None:
    """"Mon-Fri 8:30-4:30" style summary, or the first day's hours when days differ."""
    if not opening_hours:
        return None
    days = [d for d in WEEKDAYS if opening_hours.get(d)]
    if not days:
        return None
    first = opening_hours[days[0]]
    if len(days) == 7 and all(opening_hours[d] == [["00:00", "24:00"]] for d in days):
        return "Open 24 hours"
    fmt = lambda hhmm: datetime.strptime("00:00" if hhmm == "24:00" else hhmm, "%H:%M").strftime(
        "%I:%M %p").lstrip("0").replace(":00", "")
    span = ", ".join(f"{fmt(a)}-{fmt(b)}" for a, b in first)
    same = all(opening_hours[d] == first for d in days)
    label = f"{days[0].title()}-{days[-1].title()}" if len(days) > 1 else days[0].title()
    return f"{label} {span}" if same else f"{days[0].title()} {span} (hours vary)"


def initials(name: str) -> str:
    words = [w for w in name.replace("(demo)", "").replace("Dr.", "").split() if w[:1].isalpha()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def provider_out(c: Clinic, now: datetime | None = None, distance_from: str | None = None) -> dict[str, Any]:
    slots = open_slots(c, now)
    km = round(c.distance_km, 1) if c.distance_km is not None else None
    return {
        "id": c.id,
        "type": KIND_TYPES.get(c.kind or "", "clinic"),
        "name": c.name,
        "specialty": SPECIALTY_LABELS.get(c.specialty, c.specialty.replace("_", " ").title()),
        "clinic": c.name,
        "address": c.address,
        "phone": c.phone,
        "website": c.website,
        "hours": hours_summary(c.opening_hours) or c.hours,
        "distance": f"{km} km" if km is not None else None,
        "distance_km": km,
        "accepting_new": None,
        "languages": [],
        "glyph": initials(c.name),
        "slots": slots,
        "next_slot": slot_label(slots[0] if slots else None),
        "fictional": c.fictional,
        "distance_from": distance_from if km is not None else None,
    }


def _origin(db: Session, user: User, lat: float | None, lng: float | None) -> tuple[float | None, float | None, str | None]:
    """Where distances are measured from: ``device`` (the location the app sent), else ``home``
    (the profile address / postal code, geocoded once and kept on the profile; editing the address
    clears it), else nowhere."""
    if lat is not None and lng is not None:
        return lat, lng, "device"
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if profile is None:
        return None, None, None
    if profile.lat is None or profile.lng is None:
        from app.integrations.google_places import places_configured
        from app.services.booking.discovery import resolve_origin

        if not places_configured():  # the offline geocoder only knows "somewhere in Toronto"
            return None, None, None
        origin, _, _ = resolve_origin(profile)
        if origin is None:
            return None, None, None
        profile.lat, profile.lng = origin
        db.commit()
    return profile.lat, profile.lng, "home"


def _reason(card: dict[str, Any]) -> str:
    """The findings as sentences ("7-day home BP averaged 146/92. A device recorded ..."), else the headline."""
    texts = [d["text"].rstrip(". ") for d in card.get("drivers", []) if d.get("text")]
    return ". ".join(texts) + "." if texts else (card.get("headline") or "")


class _Origin:
    """What clinics.find_clinics needs from a profile: a location."""

    def __init__(self, lat: float | None, lng: float | None):
        self.lat, self.lng = lat, lng


def list_providers(db: Session, user: User, lat: float | None = None, lng: float | None = None,
                   specialty: str | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Clinics, doctors and hospitals nearest first (the directory's specialties, or one). Real
    Google Places results when a Maps key is set (none until there is a location), else the
    fictional seed list."""
    from app.integrations.google_places import places_configured

    lat, lng, source = _origin(db, user, lat, lng)
    if specialty:
        found = clinic_directory.find_clinics(_Origin(lat, lng), specialty)
    elif places_configured():
        merged: dict[str, Clinic] = {}
        for s in DIRECTORY_SPECIALTIES:
            for c in clinic_directory.find_clinics(_Origin(lat, lng), s):
                merged.setdefault(c.place_id or c.id, c)  # a place found by two searches keeps the first
        found = clinic_directory.rank_by_distance(list(merged.values()), lat, lng)[:DIRECTORY_LIMIT]
    else:
        found = clinic_directory.rank_by_distance(clinic_directory.load_seed_clinics(), lat, lng)
    return [provider_out(c, now, source) for c in found]


def recommendations(db: Session, user: User, lat: float | None = None, lng: float | None = None,
                    now: datetime | None = None) -> list[dict[str, Any]]:
    """The nearest clinic for each pathway with a finding, highest priority first, with the reason."""
    from app.services.dashboard_service import latest_cards

    lat, lng, source = _origin(db, user, lat, lng)
    cards = sorted((c for c in latest_cards(db, user) if c.get("level") in FLAGGED),
                   key=lambda c: -(c.get("priority_score") or 0))
    wanted = [(clinic_directory.specialty_for_pathway(c["pathway"]), _reason(c)) for c in cards]
    if not wanted:
        wanted = [("family_medicine", "Nothing needs a follow-up right now; a routine check-up keeps your record current.")]
    out, seen = [], set()
    for specialty, reason in wanted:
        for clinic in clinic_directory.find_clinics(_Origin(lat, lng), specialty)[:1]:
            if clinic.id in seen:
                continue
            seen.add(clinic.id)
            out.append({**provider_out(clinic, now, source), "reason": reason})
    return out


# ---------------------------------------------------------------- bookings

SNAPSHOT_FIELDS = ("name", "specialty", "clinic", "address", "phone", "glyph", "distance")


def _snapshot(provider_id: str, sent: dict[str, Any] | None) -> dict[str, Any]:
    """The provider as it should be remembered: from the directory, else what the app sent."""
    for clinic in clinic_directory.load_seed_clinics():
        if clinic.id == provider_id:
            p = provider_out(clinic)
            return {k: p.get(k) for k in SNAPSHOT_FIELDS}
    if sent and sent.get("name"):
        return {k: (str(sent[k])[:300] if sent.get(k) is not None else None) for k in SNAPSHOT_FIELDS}
    raise ProviderNotFound(provider_id)


def booking_out(b: VisitBooking) -> dict[str, Any]:
    return {"id": b.id, "provider_id": b.provider_id, "provider": b.provider, "slot": b.slot,
            "slot_label": slot_label(b.slot), "note": b.note, "status": b.status, "created_at": b.created_at}


def book(db: Session, user: User, provider_id: str, slot: str | None, note: str | None,
         provider: dict[str, Any] | None = None) -> dict[str, Any]:
    """Book a visit; the same provider and time again returns the existing booking."""
    snapshot = _snapshot(provider_id, provider)
    existing = db.scalar(select(VisitBooking).where(
        VisitBooking.user_id == user.id, VisitBooking.provider_id == provider_id,
        VisitBooking.slot.is_(None) if slot is None else VisitBooking.slot == slot,
        VisitBooking.status == "requested"))
    if existing:
        return booking_out(existing)
    row = VisitBooking(user_id=user.id, provider_id=provider_id, provider=snapshot, slot=slot, note=note)
    db.add(row)
    db.flush()
    audit(db, user.id, "create", f"visit_booking:{row.id}", {"provider_id": provider_id, "slot": slot})
    db.commit()
    return booking_out(row)


def list_bookings(db: Session, user: User) -> list[dict[str, Any]]:
    rows = db.scalars(select(VisitBooking).where(VisitBooking.user_id == user.id, VisitBooking.status == "requested")
                      .order_by(VisitBooking.slot.is_(None), VisitBooking.slot, VisitBooking.created_at)).all()
    return [booking_out(r) for r in rows]


def cancel_booking(db: Session, user: User, booking_id: str) -> None:
    row = db.get(VisitBooking, booking_id)
    if row is None or row.user_id != user.id or row.status != "requested":
        raise BookingNotFound(booking_id)
    row.status, row.cancelled_at = "cancelled", datetime.now(timezone.utc)
    audit(db, user.id, "cancel", f"visit_booking:{booking_id}")
    db.commit()
