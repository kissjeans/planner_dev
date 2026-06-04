"""Concrete weekend/holiday calendar and working-day arithmetic helpers."""

from __future__ import annotations

from datetime import date, timedelta

from planner.domain.calendar.ports import WorkingCalendar

_WEEKEND = frozenset({5, 6})  # Saturday, Sunday


class WeekendCalendar:
    """Working-day calendar: weekends and an explicit holiday set are off."""

    def __init__(self, holidays: frozenset[date] = frozenset()) -> None:
        self._holidays = holidays

    def is_working_day(self, day: date) -> bool:
        return day.weekday() not in _WEEKEND and day not in self._holidays

    def next_working_day(self, day: date) -> date:
        d = day + timedelta(days=1)
        while not self.is_working_day(d):
            d += timedelta(days=1)
        return d

    def business_days_between(self, a: date, b: date) -> int:
        """Count working days in the half-open interval ``[a, b)``."""
        if b <= a:
            return 0
        n = 0
        d = a
        while d < b:
            if self.is_working_day(d):
                n += 1
            d += timedelta(days=1)
        return n


def first_working_day(cal: WorkingCalendar, start: date) -> date:
    """Return ``start`` if it is a working day, else the next working day."""
    if cal.is_working_day(start):
        return start
    return cal.next_working_day(start)


def nth_working_day(cal: WorkingCalendar, start: date, n: int) -> date:
    """Return the ``n``-th working day on/after ``start`` (1-indexed).

    ``n == 1`` yields :func:`first_working_day`.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    day = first_working_day(cal, start)
    for _ in range(n - 1):
        day = cal.next_working_day(day)
    return day
