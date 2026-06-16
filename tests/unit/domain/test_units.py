"""Unit tests for hours→working-days display conversion (spec §6 hybrid)."""

from __future__ import annotations

import pytest

from planner.domain.units import hours_to_working_days


@pytest.mark.parametrize(
    ("hours", "capacity", "expected"),
    [
        (8, 8, 1),  # exactly one day
        (16, 8, 2),  # two full days
        (4, 8, 1),  # part of a day rounds up
        (9, 8, 2),  # spills into a second day → rounds up
        (0, 8, 0),  # no load → zero days
        (10, 0, 10),  # zero capacity (weekend/vacation): fall back to hours count
    ],
)
def test_hours_to_working_days_rounds_up(hours, capacity, expected):
    assert hours_to_working_days(hours, capacity) == expected
