"""Unit tests for the slot conversion (hybrid hours↔slots model)."""

from planner.domain.slots import SLOT_HOURS, hours_to_slots


def test_slot_hours_constant():
    assert SLOT_HOURS == 4


def test_zero_and_negative_hours_are_zero_slots():
    assert hours_to_slots(0) == 0
    assert hours_to_slots(-5) == 0


def test_full_day_is_two_slots():
    assert hours_to_slots(8) == 2


def test_partial_hours_round_up():
    assert hours_to_slots(1) == 1
    assert hours_to_slots(4) == 1
    assert hours_to_slots(5) == 2


def test_design_unit_four_slots():
    assert hours_to_slots(16) == 4
