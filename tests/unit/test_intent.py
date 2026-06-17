"""Unit tests for the Intent discriminated union (spec section 6.1)."""

from pydantic import TypeAdapter

from planner.domain.intent import (
    WRITE_KINDS,
    AddProjectIntent,
    CaptureTaskIntent,
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


def test_discriminates_capture_task():
    i = _ta.validate_python(
        {"kind": "capture_task", "task_title": "подготовить бриф",
         "assignee_names": ["Андрей"], "project_name": "МТС"}
    )
    assert isinstance(i, CaptureTaskIntent)
    assert i.deadline is None


def test_write_kinds_excludes_read_intents():
    assert "add_project" in WRITE_KINDS
    assert "load" not in WRITE_KINDS
    assert "clarify" not in WRITE_KINDS
    assert "what_if" not in WRITE_KINDS  # what-if is read-only -> open to all
    assert "capture_task" in WRITE_KINDS  # capture writes -> gated to admins


def test_capture_task_rejects_blank_title():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CaptureTaskIntent(task_title="")


def test_capture_task_accepts_multiple_assignees():
    from planner.domain.intent import CaptureTaskIntent

    i = CaptureTaskIntent(task_title="ресёрч по МТС", assignee_names=["Андрей", "Рай"])
    assert i.assignee_names == ["Андрей", "Рай"]


def test_capture_task_assignees_default_empty():
    from planner.domain.intent import CaptureTaskIntent

    i = CaptureTaskIntent(task_title="бриф")
    assert i.assignee_names == []


def test_capture_task_enrichment_defaults():
    from planner.domain.intent import CaptureTaskIntent

    i = CaptureTaskIntent(task_title="бриф")
    assert i.est_hours is None
    assert i.required_skills == []


def test_capture_task_carries_enrichment_fields():
    from planner.domain.intent import CaptureTaskIntent

    i = CaptureTaskIntent(
        task_title="сделать макет", est_hours=12, required_skills=["дизайн"]
    )
    assert i.est_hours == 12
    assert i.required_skills == ["дизайн"]


def test_load_intent_coerces_dict_date_range():
    """Claude emits date_range as a {from,to} object — coerce to the tuple."""
    from datetime import date

    from planner.domain.intent import LoadIntent

    i = LoadIntent(date_range={"from": "2026-06-22", "to": "2026-06-28"})
    assert i.date_range == (date(2026, 6, 22), date(2026, 6, 28))
    assert LoadIntent(date_range=None).date_range is None
    assert LoadIntent(date_range={"from": "2026-06-22"}).date_range is None
