"""Unit tests for WhatIfUseCase (spec section 7.2 / 14)."""

from datetime import date
from uuid import uuid4

from planner.app.what_if import WhatIfUseCase, apply_operation
from planner.domain.calendar.rules import WeekendCalendar
from planner.domain.intent import WhatIfIntent
from planner.domain.models import Person, PlanRequest, Task
from planner.domain.solver.greedy import GreedySolver

START = date(2026, 6, 1)
CAL = WeekendCalendar()


def _task(pid, name):
    return Task(id=uuid4(), name=name, duration_hours=8, allowed_person_ids=(pid,))


def _base_request():
    p = Person(id=uuid4(), name="Solo", capacity_h=8)
    t1, t2 = _task(p.id, "t1"), _task(p.id, "t2")
    return PlanRequest(
        people=(p,),
        tasks=(t1, t2),
        dependencies=(),
        horizon_start=START,
    )


def test_add_person_moves_a_queued_task():
    req = _base_request()
    uc = WhatIfUseCase(GreedySolver(CAL))
    diff = uc.execute(req, WhatIfIntent(operation="add_person", person_name="Помощник"))
    # The second task, previously queued to day 2, can now run in parallel.
    assert len(diff.moved_tasks) >= 1


def test_apply_shift_deadline_replaces_deadline():
    req = _base_request()
    out = apply_operation(req, WhatIfIntent(operation="shift_deadline",
                                            new_deadline=date(2026, 7, 1)))
    assert out.deadline == date(2026, 7, 1)


def test_apply_drop_project_empties_tasks():
    req = _base_request()
    out = apply_operation(req, WhatIfIntent(operation="drop_project"))
    assert out.tasks == ()


def test_apply_switch_to_lite_is_noop():
    req = _base_request()
    out = apply_operation(req, WhatIfIntent(operation="switch_to_lite"))
    assert out is req
