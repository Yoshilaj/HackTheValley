"""Appointment slots: user's FreeBusy ∩ clinic opening hours (booking-v2 spec §1.3).

    slots = candidate_slots(opening_hours, clinic_tz, busy=[(start, end), ...], now=now)
    picks = pick_spread(slots, 3)
    plan  = plan_slots(clinic, calendar=cal, use_freebusy=True, now=now)   # SlotPlan

Rules: 30-minute slots aligned to each opening interval's start, in the clinic's timezone;
start ≥ now + 24 h; next 14 days; a slot must lie fully inside one opening interval and must not
overlap any busy block (busy blocks are tz-aware, any zone). Closed days have no slots. Without
known opening hours, Mon–Fri 09:00–17:00 in the clinic timezone is assumed (noted in the plan).
Without calendar access, opening hours alone are used. `pick_spread` proposes 3 slots on
different days spread over the first available days, alternating morning / afternoon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.integrations import Slot
from app.integrations.timezones import resolve_timezone

Busy = tuple[datetime, datetime]
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_HOURS: dict[str, list[list[str]]] = {k: [["09:00", "17:00"]] for k in WEEKDAY_KEYS[:5]}
SLOT_MINUTES = 30
LEAD_TIME = timedelta(hours=24)
HORIZON_DAYS = 14
DEFAULT_TZ = "America/Toronto"


def clinic_timezone(clinic: Any, fallback: str | None = None) -> str:
    """The clinic's IANA zone: explicit, else from its address / UTC offset, else `fallback` (the
    user's zone), else America/Toronto (app/integrations/timezones.py)."""
    return resolve_timezone(clinic, fallback)


def zone_or_default(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _parse_hm(value: str) -> tuple[int, int]:
    h, m = str(value).split(":")[:2]
    return int(h), int(m)


def opening_intervals(hours: dict[str, list[list[str]]] | None, day: date, tz: str | None) -> list[tuple[datetime, datetime]]:
    """Opening intervals for one local calendar day as tz-aware datetimes ("24:00" = midnight)."""
    zone = zone_or_default(tz)
    out = []
    for pair in (hours or {}).get(WEEKDAY_KEYS[day.weekday()], []) or []:
        try:
            (oh, om), (ch, cm) = _parse_hm(pair[0]), _parse_hm(pair[1])
        except (ValueError, IndexError, TypeError):
            continue
        start = datetime.combine(day, time(oh, om), tzinfo=zone)
        end = (datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone) if (ch, cm) >= (24, 0)
               else datetime.combine(day, time(ch, cm), tzinfo=zone))
        if end > start:
            out.append((start, end))
    return out


def overlaps(start: datetime, end: datetime, busy: list[Busy]) -> bool:
    return any(b_start < end and start < b_end for b_start, b_end in busy)


def within_opening_hours(slot: Slot, hours: dict[str, list[list[str]]] | None, tz: str | None) -> bool:
    """True when [slot.start, slot.end) lies inside one opening interval of the clinic."""
    zone = zone_or_default(tz)
    local = slot.start.astimezone(zone)
    return any(o <= slot.start and slot.end <= c for o, c in opening_intervals(hours or DEFAULT_HOURS, local.date(), tz))


def candidate_slots(hours: dict[str, list[list[str]]] | None, tz: str | None, busy: list[Busy] | None = None,
                    now: datetime | None = None, days: int = HORIZON_DAYS, slot_minutes: int = SLOT_MINUTES,
                    lead: timedelta = LEAD_TIME) -> list[Slot]:
    """All free slots (see module docstring), chronological."""
    zone = zone_or_default(tz)
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    earliest = now + lead
    horizon = now + timedelta(days=days + 1)
    step = timedelta(minutes=slot_minutes)
    busy = list(busy or [])
    out: list[Slot] = []
    for offset in range(0, days + 2):
        day = (now + timedelta(days=offset)).date()
        for o, c in opening_intervals(hours if hours is not None else DEFAULT_HOURS, day, tz):
            cursor = o
            while cursor + step <= c:
                end = cursor + step
                if cursor >= earliest and cursor < horizon and not overlaps(cursor, end, busy):
                    out.append(Slot(cursor, end))
                cursor = end
    out.sort(key=lambda s: s.start)
    return out


def pick_spread(slots: list[Slot], n: int = 3, tz: str | None = None, first_days: int = 6) -> list[Slot]:
    """n slots on different days, spread over the first `first_days` days that have slots,
    alternating preferred local times 10:00 / 14:00 / 11:00. Fewer days -> fill from any slot."""
    if not slots:
        return []
    zone = zone_or_default(tz) if tz else slots[0].start.tzinfo
    by_day: dict[date, list[Slot]] = {}
    for s in slots:
        by_day.setdefault(s.start.astimezone(zone).date(), []).append(s)
    days = sorted(by_day)[:max(n, first_days)]
    if len(days) >= n:
        idx = sorted({round(i * (len(days) - 1) / (n - 1)) for i in range(n)}) if n > 1 else [0]
        chosen_days = [days[i] for i in idx]
    else:
        chosen_days = days
    targets = (10 * 60, 14 * 60, 11 * 60)
    picked: list[Slot] = []
    for i, d in enumerate(chosen_days):
        target = targets[i % len(targets)]
        best = min(by_day[d], key=lambda s: abs(s.start.astimezone(zone).hour * 60 + s.start.astimezone(zone).minute
                                                 - target))
        picked.append(best)
    for s in slots:  # fill when fewer distinct days than n
        if len(picked) >= n:
            break
        if s not in picked:
            picked.append(s)
    return sorted(picked[:n], key=lambda s: s.start)


@dataclass
class SlotPlan:
    proposals: list[Slot]
    available: list[Slot]
    source: str                         # calendar_freebusy | opening_hours_only | freebusy_error
    clinic_tz: str
    notes: list[str] = field(default_factory=list)

    def as_log(self) -> dict[str, Any]:
        return {"source": self.source, "clinic_tz": self.clinic_tz, "available": len(self.available),
                "proposals": [s.as_dict() for s in self.proposals], "notes": self.notes}


def calendar_busy(calendar: Any, now: datetime, days: int = HORIZON_DAYS) -> list[Busy]:
    """Busy blocks from a calendar client (`busy(time_min, time_max)`)."""
    return list(calendar.busy(now, now + timedelta(days=days + 2)))


def plan_slots(clinic: Any, *, calendar: Any = None, use_freebusy: bool = False, now: datetime | None = None,
               n: int = 3, fallback_tz: str | None = None) -> SlotPlan:
    """Slots for one clinic (Clinic or dict): opening hours ∩ FreeBusy (when allowed)."""
    get = (lambda k: clinic.get(k)) if isinstance(clinic, dict) else (lambda k: getattr(clinic, k, None))
    tz = clinic_timezone(clinic, fallback_tz)
    hours = get("opening_hours")
    now = now or datetime.now(timezone.utc)
    notes = []
    if not hours:
        notes.append("clinic opening hours unknown: assuming Mon–Fri 09:00–17:00")
    busy: list[Busy] = []
    source = "opening_hours_only"
    if use_freebusy and calendar is not None:
        try:
            busy = calendar_busy(calendar, now)
            source = "calendar_freebusy"
        except Exception as exc:
            notes.append(f"FreeBusy failed ({type(exc).__name__}); using opening hours only")
            source = "freebusy_error"
    available = candidate_slots(hours, tz, busy, now)
    return SlotPlan(proposals=pick_spread(available, n, tz), available=available, source=source,
                    clinic_tz=tz, notes=notes)


def slot_is_bookable(slot: Slot, clinic: Any, *, calendar: Any = None, use_freebusy: bool = False,
                     now: datetime | None = None, fallback_tz: str | None = None) -> tuple[bool, str]:
    """(ok, reason) for a clinic counter-offer: in the future, within opening hours, and free
    in the user's calendar. Without calendar access the slot cannot be verified -> not ok."""
    get = (lambda k: clinic.get(k)) if isinstance(clinic, dict) else (lambda k: getattr(clinic, k, None))
    now = now or datetime.now(timezone.utc)
    if slot.start <= now:
        return False, "slot is in the past"
    if not within_opening_hours(slot, get("opening_hours"), clinic_timezone(clinic, fallback_tz)):
        return False, "outside the clinic's opening hours"
    if not use_freebusy or calendar is None:
        return False, "calendar not available to check the slot"
    try:
        busy = list(calendar.busy(slot.start - timedelta(minutes=1), slot.end + timedelta(minutes=1)))
    except Exception as exc:
        return False, f"FreeBusy failed ({type(exc).__name__})"
    if overlaps(slot.start, slot.end, busy):
        return False, "conflicts with the user's calendar"
    return True, "free in the calendar and within opening hours"
