"""Calendar port consumed by the solver.

The solver is synchronous and pure, so it depends on this synchronous
``WorkingCalendar`` protocol rather than the async HTTP calendar adapters
(those live in ``infra/calendar`` and pre-resolve working days for the solver).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol


class WorkingCalendar(Protocol):
    """Synchronous working-day calendar used during scheduling."""

    def is_working_day(self, day: date) -> bool: ...

    def next_working_day(self, day: date) -> date: ...

    def business_days_between(self, a: date, b: date) -> int: ...
