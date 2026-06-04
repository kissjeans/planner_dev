"""Offline snapshot calendar (spec section 10 / 2.2).

Wraps the domain :class:`WeekendCalendar` with a hardcoded Russian public
holiday set. In a later sprint the live ``isdayoff.ru`` adapter refreshes this
snapshot; for the solver it is the always-available offline fallback.
"""

from __future__ import annotations

from datetime import date

from planner.domain.calendar.rules import WeekendCalendar

# Russian non-working public holidays for 2026 (official production calendar).
RU_HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 2, 23),
        date(2026, 3, 9),  # 8 March observed
        date(2026, 5, 1),
        date(2026, 5, 11),  # 9 May observed
        date(2026, 6, 12),
        date(2026, 11, 4),
    }
)


class SnapshotCalendar(WeekendCalendar):
    """Weekend calendar seeded with a public-holiday snapshot."""

    def __init__(self, holidays: frozenset[date] = RU_HOLIDAYS_2026) -> None:
        super().__init__(holidays)
