"""Hours→days display conversion (spec §6 hybrid).

The solver does exact math in hours internally; user-facing strings show
working days/windows instead ("дизайн ≈ 2 рабочих дня"). This helper is the
single place that maps an hour count onto whole working days.
"""

from __future__ import annotations

import math


def hours_to_working_days(hours: int, capacity_h: int) -> int:
    """Round an hour count up to whole working days at ``capacity_h`` per day.

    A zero ``capacity_h`` (weekend/vacation) has no day norm to divide by, so
    we fall back to the raw hour count rather than divide by zero.
    """
    if hours <= 0:
        return 0
    if capacity_h <= 0:
        return hours
    return math.ceil(hours / capacity_h)
