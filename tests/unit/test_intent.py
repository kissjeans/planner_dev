"""Unit tests for the Intent discriminated union (spec section 6.1)."""

from pydantic import TypeAdapter

from planner.domain.intent import (
    WRITE_KINDS,
    AddProjectIntent,
    ClarifyIntent,
    Intent,
    LoadIntent,
    VacationIntent,
    WhatIfIntent,
)

_ta = TypeAdapter(Intent)


def test_discriminates_add_project():
    i = _ta.validate_python(
        {"kind": "add_project", "title": "X", "template_code": "lite"}
    )
    assert isinstance(i, AddProjectIntent)
    assert i.deadline is None  # backward mode when not given


def test_discriminates_load():
    assert isinstance(_ta.validate_python({"kind": "load"}), LoadIntent)


def test_discriminates_what_if():
    i = _ta.validate_python({"kind": "what_if", "operation": "switch_to_lite"})
    assert isinstance(i, WhatIfIntent)


def test_discriminates_vacation():
    i = _ta.validate_python(
        {
            "kind": "vacation",
            "person_name": "Айгуль",
            "day_from": "2026-06-10",
            "day_to": "2026-06-12",
        }
    )
    assert isinstance(i, VacationIntent)
    assert i.capacity_h == 0


def test_discriminates_clarify():
    assert isinstance(_ta.validate_python({"kind": "clarify"}), ClarifyIntent)


def test_write_kinds_excludes_read_intents():
    assert "add_project" in WRITE_KINDS
    assert "load" not in WRITE_KINDS
    assert "clarify" not in WRITE_KINDS
