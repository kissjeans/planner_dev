"""Integration tests for calendar adapters (spec 12.1).

Snapshot tests run offline; isdayoff.ru live fetch is covered by the
parse_year_offdays unit-level function to avoid network dependency in CI.
"""

from __future__ import annotations

from datetime import date

from planner.infra.calendar.isdayoff import (
    holidays_from_offdays,
    parse_year_offdays,
)
from planner.infra.calendar.snapshot import SnapshotCalendar

# --- SnapshotCalendar (synchronous CalendarPort) ---

def test_snapshot_new_year_is_holiday():
    cal = SnapshotCalendar()
    assert not cal.is_working_day(date(2026, 1, 1))


def test_snapshot_regular_monday_is_working():
    cal = SnapshotCalendar()
    # 2 June 2026 is a Monday, no holiday
    assert cal.is_working_day(date(2026, 6, 2))


def test_snapshot_saturday_is_not_working():
    cal = SnapshotCalendar()
    assert not cal.is_working_day(date(2026, 6, 6))


def test_snapshot_business_days_between_week():
    cal = SnapshotCalendar()
    # Mon 2 Jun to Fri 6 Jun exclusive = 4 working days
    count = cal.business_days_between(date(2026, 6, 2), date(2026, 6, 6))
    assert count == 4


def test_snapshot_next_working_day_friday_to_monday():
    cal = SnapshotCalendar()
    # Fri 5 Jun → Sat/Sun weekend → Mon 8 Jun
    nwd = cal.next_working_day(date(2026, 6, 5))
    assert nwd == date(2026, 6, 8)


# --- parse_year_offdays / holidays_from_offdays ---

def test_parse_year_offdays_marks_correct_dates():
    result = parse_year_offdays(2026, "101")
    assert date(2026, 1, 1) in result
    assert date(2026, 1, 2) not in result
    assert date(2026, 1, 3) in result


def test_holidays_from_offdays_keeps_only_weekdays():
    # 2026-01-03 is Saturday; 2026-01-05 is Monday
    offdays: frozenset[date] = frozenset({date(2026, 1, 3), date(2026, 1, 5)})
    holidays = holidays_from_offdays(offdays)
    assert date(2026, 1, 3) not in holidays   # Saturday excluded
    assert date(2026, 1, 5) in holidays        # Monday holiday kept


def test_snapshot_built_from_isdayoff_data():
    """Simulate what IsDayOffClient.build_snapshot does with known data."""
    # 5 Jan 2026 is a Monday (extra New Year holiday in Russia)
    bitstring = ("1" * 9).ljust(365, "0")
    offdays = parse_year_offdays(2026, bitstring)
    holidays = holidays_from_offdays(offdays)
    cal = SnapshotCalendar(holidays)
    # 5 Jan 2026 Monday should be off
    assert not cal.is_working_day(date(2026, 1, 5))
