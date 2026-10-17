"""Google Calendar: FreeBusy -> free slots, and tentative / confirmed events.

    cal = get_calendar_client(db, user.id)          # CalendarClient or FakeCalendar
    cal.connected                                  # False = no Google credential (demo fake)
    slots = cal.free_slots(days=14, business_hours=(9, 17), slot_minutes=30, tz="America/Toronto")
    event_id = cal.create_event(summary, start, end, description, tentative=True)
    cal.update_event(event_id, status="confirmed", start=..., end=...)
    cal.delete_event(event_id)

Slot computation is a pure function (`compute_free_slots`) shared by the real client and
`FakeCalendar` so tests exercise the same logic.
"""

from __future__ import annotations

import itertools
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations import Slot

log = get_logger(__name__)

Busy = tuple[datetime, datetime]


def compute_free_slots(
    busy: list[Busy],
    days: int = 14,
    business_hours: tuple[int, int] = (9, 17),
    slot_minutes: int = 30,
    tz: str = "America/Toronto",
    now: datetime | None = None,
    weekdays_only: bool = True,
) -> list[Slot]:
    """Free slots on the next `days` calendar days (starting tomorrow, local `tz`), inside
    `business_hours` [start_h, end_h), not overlapping any busy block. Weekdays only by default."""
    zone = ZoneInfo(tz)
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    start_h, end_h = business_hours
    step = timedelta(minutes=slot_minutes)
    out: list[Slot] = []
    for offset in range(1, days + 1):
        day: date = (now + timedelta(days=offset)).date()
        if weekdays_only and day.weekday() >= 5:
            continue
        cursor = datetime.combine(day, time(start_h), tzinfo=zone)
        day_end = datetime.combine(day, time(end_h), tzinfo=zone)
        while cursor + step <= day_end:
            end = cursor + step
            if not any(b_start < end and cursor < b_end for b_start, b_end in busy):
                out.append(Slot(cursor, end))
            cursor = end
    return out


def default_slots(tz: str = "America/Toronto", now: datetime | None = None, slot_minutes: int = 30) -> list[Slot]:
    """No calendar access: next 3 weekdays at 10:00 and 14:00 local (6 slots)."""
    zone = ZoneInfo(tz)
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    out: list[Slot] = []
    day = now.date()
    while len(out) < 6:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        for hour in (10, 14):
            start = datetime.combine(day, time(hour), tzinfo=zone)
            out.append(Slot(start, start + timedelta(minutes=slot_minutes)))
    return out


class Calendar(Protocol):
    connected: bool
    is_fake: bool

    def free_slots(self, user_cred: dict[str, Any] | None = None, days: int = 14,
                   business_hours: tuple[int, int] = (9, 17), slot_minutes: int = 30,
                   tz: str = "America/Toronto", now: datetime | None = None) -> list[Slot]: ...
    def busy(self, time_min: datetime, time_max: datetime) -> list[Busy]: ...
    def create_event(self, summary: str, start: datetime, end: datetime, description: str = "",
                     tentative: bool = True) -> str: ...
    def update_event(self, event_id: str, **changes: Any) -> bool: ...
    def delete_event(self, event_id: str) -> bool: ...


class CalendarClient:
    """Real Google Calendar client (google-api-python-client) on the user's primary calendar."""

    is_fake = False

    def __init__(self, tokens: dict[str, Any], calendar_id: str = "primary") -> None:
        self.tokens = tokens
        self.calendar_id = calendar_id
        self.connected = bool(tokens.get("access_token") or tokens.get("refresh_token"))
        self._service = None

    def _svc(self, tokens: dict[str, Any] | None = None):
        if self._service is None or tokens is not None:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            from app.integrations.google_oauth import SCOPES, TOKEN_URL

            t = tokens or self.tokens
            creds = Credentials(
                token=t.get("access_token"), refresh_token=t.get("refresh_token"), token_uri=TOKEN_URL,
                client_id=settings.google_client_id, client_secret=settings.google_client_secret, scopes=SCOPES,
            )
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def free_slots(self, user_cred=None, days=14, business_hours=(9, 17), slot_minutes=30,
                   tz="America/Toronto", now=None) -> list[Slot]:
        now = now or datetime.now(timezone.utc)
        if user_cred is not None:
            self._svc(user_cred)
        busy = self.busy(now, now + timedelta(days=days + 1))
        return compute_free_slots(busy, days, business_hours, slot_minutes, tz, now)

    def busy(self, time_min, time_max) -> list[Busy]:
        """FreeBusy busy blocks of the primary calendar between time_min and time_max (tz-aware)."""
        body = {"timeMin": time_min.isoformat(), "timeMax": time_max.isoformat(), "items": [{"id": self.calendar_id}]}
        resp = self._svc().freebusy().query(body=body).execute()
        busy_raw = resp.get("calendars", {}).get(self.calendar_id, {}).get("busy", [])
        return [(datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                 datetime.fromisoformat(b["end"].replace("Z", "+00:00"))) for b in busy_raw]

    def create_event(self, summary, start, end, description="", tentative=True) -> str:
        body = {
            "summary": summary, "description": description,
            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
            "status": "tentative" if tentative else "confirmed",
            "transparency": "transparent" if tentative else "opaque",
        }
        event = self._svc().events().insert(calendarId=self.calendar_id, body=body).execute()
        return event["id"]

    def update_event(self, event_id, **changes) -> bool:
        body: dict[str, Any] = {}
        if "summary" in changes:
            body["summary"] = changes["summary"]
        if "description" in changes:
            body["description"] = changes["description"]
        if "status" in changes:
            body["status"] = changes["status"]
            body["transparency"] = "opaque" if changes["status"] == "confirmed" else "transparent"
        if changes.get("start"):
            body["start"] = {"dateTime": changes["start"].isoformat()}
        if changes.get("end"):
            body["end"] = {"dateTime": changes["end"].isoformat()}
        self._svc().events().patch(calendarId=self.calendar_id, eventId=event_id, body=body).execute()
        return True

    def delete_event(self, event_id) -> bool:
        self._svc().events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        return True


class FakeCalendar:
    """In-memory calendar. `busy` (ctor) = [(start, end)] blocks kept in `busy_blocks`;
    `events` = {event_id: dict}.
    `connected=False` mimics "no Google credential" (the booking agent then skips FreeBusy
    and does not create events)."""

    is_fake = True
    _ids = itertools.count(1)

    def __init__(self, busy: list[Busy] | None = None, connected: bool = True) -> None:
        self.busy_blocks = list(busy or [])
        self.events: dict[str, dict[str, Any]] = {}
        self.connected = connected
        self.freebusy_calls = 0

    def free_slots(self, user_cred=None, days=14, business_hours=(9, 17), slot_minutes=30,
                   tz="America/Toronto", now=None) -> list[Slot]:
        self.freebusy_calls += 1
        return compute_free_slots(self._blocks(), days, business_hours, slot_minutes, tz, now)

    def _blocks(self) -> list[Busy]:
        return self.busy_blocks + [(e["start"], e["end"]) for e in self.events.values()
                                   if e["status"] not in ("cancelled", "tentative")]

    def busy(self, time_min, time_max) -> list[Busy]:
        """Busy blocks overlapping [time_min, time_max): `busy_blocks` + confirmed events
        (tentative events are transparent, like the real client creates them)."""
        self.freebusy_calls += 1
        return [(s, e) for s, e in self._blocks() if s < time_max and time_min < e]

    def create_event(self, summary, start, end, description="", tentative=True) -> str:
        event_id = f"fake_evt_{next(self._ids)}"
        self.events[event_id] = {"summary": summary, "start": start, "end": end, "description": description,
                                 "status": "tentative" if tentative else "confirmed"}
        return event_id

    def update_event(self, event_id, **changes) -> bool:
        event = self.events.get(event_id)
        if event is None:
            return False
        event.update({k: v for k, v in changes.items() if v is not None})
        return True

    def delete_event(self, event_id) -> bool:
        return self.events.pop(event_id, None) is not None


def get_calendar_client(db: Session, user_id: str) -> Calendar:
    """CalendarClient when Google OAuth is configured and the user has a stored (non-fake)
    credential; otherwise FakeCalendar(connected=<user connected via the fake OAuth flow>)."""
    from app.integrations.google_oauth import fresh_tokens, google_configured

    tokens = fresh_tokens(db, user_id)
    if tokens and google_configured() and not tokens.get("fake"):
        return CalendarClient(tokens)
    return FakeCalendar(connected=bool(tokens))
