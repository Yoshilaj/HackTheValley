"""Slots = FreeBusy ∩ clinic opening hours (clinic timezone)."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.integrations import Slot
from app.integrations.google_calendar import FakeCalendar
from app.services.booking import slots as sl

TOR = ZoneInfo("America/Toronto")
VAN = ZoneInfo("America/Vancouver")
# Friday 2026-10-16 08:00 Toronto
NOW = datetime(2026, 10, 16, 8, 0, tzinfo=TOR)
HOURS = {"mon": [["09:00", "12:00"], ["13:00", "17:00"]], "tue": [["09:00", "17:00"]], "sat": [["10:00", "11:00"]]}


def test_slots_inside_opening_hours_closed_days_and_lead_time():
    out = sl.candidate_slots(HOURS, "America/Toronto", now=NOW)
    assert out and all(s.end - s.start == timedelta(minutes=30) for s in out)
    days = {s.start.astimezone(TOR).date().isoformat() for s in out}
    assert "2026-10-16" not in days and "2026-10-18" not in days  # Friday closed (and <24 h), Sunday closed
    assert "2026-10-17" in days  # Saturday 10-11 opens 26 h later
    assert all(s.start >= NOW + timedelta(hours=24) for s in out)
    mon = [s for s in out if s.start.astimezone(TOR).date().isoformat() == "2026-10-19"]
    assert len(mon) == 14 and not any(s.start.astimezone(TOR).hour == 12 for s in mon)  # lunch closed
    assert max(s.start for s in out) < NOW + timedelta(days=15)
    assert all(sl.within_opening_hours(s, HOURS, "America/Toronto") for s in out)


def test_busy_blocks_in_other_timezones_are_respected():
    # 14:00-15:00 UTC on Monday = 10:00-11:00 Toronto (EDT)
    busy = [(datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc), datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc))]
    out = sl.candidate_slots(HOURS, "America/Toronto", busy=busy, now=NOW)
    mon_hours = [s.start.astimezone(TOR).strftime("%H:%M") for s in out if s.start.astimezone(TOR).day == 19]
    assert "10:00" not in mon_hours and "10:30" not in mon_hours and "09:30" in mon_hours and "11:00" in mon_hours


def test_clinic_timezone_differs_from_user():
    out = sl.candidate_slots({"mon": [["09:00", "10:00"]]}, "America/Vancouver", now=NOW)
    first = out[0].start
    assert first.utcoffset() == timedelta(hours=-7)  # PDT
    assert first.astimezone(TOR).hour == 12  # 09:00 Vancouver = 12:00 Toronto
    assert not sl.within_opening_hours(Slot(first.astimezone(TOR).replace(hour=9), first.astimezone(TOR).replace(
        hour=9, minute=30)), {"mon": [["09:00", "10:00"]]}, "America/Vancouver")


def test_dst_change_keeps_local_wall_clock():
    now = datetime(2026, 10, 28, 8, 0, tzinfo=TOR)  # DST ends 2026-11-01
    out = sl.candidate_slots({"mon": [["09:00", "09:30"]]}, "America/Toronto", now=now)
    assert [s.start.astimezone(TOR).hour for s in out] == [9, 9]
    assert {s.start.utcoffset() for s in out} == {timedelta(hours=-5)}  # Nov 2 and Nov 9 are EST


def test_unknown_hours_default_weekdays_and_24h():
    out = sl.candidate_slots(None, "America/Toronto", now=NOW)
    assert {s.start.astimezone(TOR).weekday() for s in out} <= {0, 1, 2, 3, 4}
    allday = sl.candidate_slots({"mon": [["00:00", "24:00"]]}, "America/Toronto", now=NOW, days=3)
    assert len(allday) == 48


def test_pick_spread_three_days_alternating_times():
    picks = sl.pick_spread(sl.candidate_slots(HOURS, "America/Toronto", now=NOW), 3, "America/Toronto")
    assert len(picks) == 3 and len({p.start.astimezone(TOR).date() for p in picks}) == 3
    assert picks == sorted(picks, key=lambda s: s.start)
    one_day = sl.candidate_slots({"mon": [["09:00", "11:00"]]}, "America/Toronto", now=NOW, days=3)
    assert len(sl.pick_spread(one_day, 3, "America/Toronto")) == 3  # filled from the same day


def test_plan_slots_with_calendar_and_counter_offer_check():
    clinic = {"opening_hours": HOURS, "timezone": "America/Toronto"}
    mon10 = datetime(2026, 10, 19, 10, 0, tzinfo=TOR)
    cal = FakeCalendar(busy=[(mon10, mon10 + timedelta(hours=1))])
    plan = sl.plan_slots(clinic, calendar=cal, use_freebusy=True, now=NOW)
    assert plan.source == "calendar_freebusy" and len(plan.proposals) == 3
    assert all(not sl.overlaps(s.start, s.end, cal.busy_blocks) for s in plan.available)
    assert sl.plan_slots(clinic, calendar=cal, use_freebusy=False, now=NOW).source == "opening_hours_only"

    ok, _ = sl.slot_is_bookable(Slot(mon10 + timedelta(hours=3), mon10 + timedelta(hours=3, minutes=30)), clinic,
                                calendar=cal, use_freebusy=True, now=NOW)
    assert ok
    busy, why = sl.slot_is_bookable(Slot(mon10, mon10 + timedelta(minutes=30)), clinic, calendar=cal,
                                    use_freebusy=True, now=NOW)
    assert not busy and "calendar" in why
    closed, why = sl.slot_is_bookable(Slot(mon10.replace(hour=12), mon10.replace(hour=12, minute=30)), clinic,
                                      calendar=cal, use_freebusy=True, now=NOW)
    assert not closed and "opening hours" in why
    no_cal, why = sl.slot_is_bookable(Slot(mon10 + timedelta(hours=3), mon10 + timedelta(hours=3, minutes=30)),
                                      clinic, calendar=None, use_freebusy=False, now=NOW)
    assert not no_cal and "calendar not available" in why
