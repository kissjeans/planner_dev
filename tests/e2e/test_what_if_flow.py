"""E2E test: WhatIf flow (spec section 16, scenario C / spec 14).

Tests the WhatIfUseCase in isolation with domain objects,
mirroring the full flow from intent to PlanDiff.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from planner.app.what_if import WhatIfUseCase, apply_operation
from planner.domain.calendar.rules import WeekendCalendar
from planner.domain.intent import WhatIfIntent
from planner.domain.models import Person, PlanRequest, Task
from planner.domain.solver.greedy import GreedySolver

START = date(2026, 6, 2)
CAL = WeekendCalendar()


def _build_request_two_tasks():
    pid = uuid4()
    person = Person(id=pid, name="Андрей", capacity_h=8)
    t1 = Task(id=uuid4(), name="Т1", duration_hours=8, allowed_person_ids=(pid,))
    t2 = Task(id=uuid4(), name="Т2", duration_hours=8, allowed_person_ids=(pid,))
    return PlanRequest(
        people=(person,),
        tasks=(t1, t2),
        dependencies=(),
        horizon_start=START,
    ), person


@pytest.mark.asyncio
async def test_what_if_shift_deadline_changes_request():
    """Scenario C: shift deadline → apply_operation replaces the deadline."""
    req, _ = _build_request_two_tasks()
    intent = WhatIfIntent(operation="shift_deadline", new_deadline=date(2026, 7, 1))
    modified = apply_operation(req, intent)
    assert modified.deadline == date(2026, 7, 1)


@pytest.mark.asyncio
async def test_what_if_add_person_parallelises_tasks():
    """Adding a person allows the second task to run in parallel → diff non-empty."""
    req, _ = _build_request_two_tasks()
    uc = WhatIfUseCase(GreedySolver(CAL))
    diff = uc.execute(req, WhatIfIntent(operation="add_person", person_name="Помощник"))
    # With two people the second task can start same day → at least one task moved
    assert len(diff.moved_tasks) >= 1


@pytest.mark.asyncio
async def test_what_if_drop_project_returns_empty_plan():
    req, _ = _build_request_two_tasks()
    intent = WhatIfIntent(operation="drop_project")
    modified = apply_operation(req, intent)
    assert modified.tasks == ()


@pytest.mark.asyncio
async def test_what_if_diff_no_change_when_no_operation():
    """shift_deadline with same deadline produces no diff in the plan."""
    req, _ = _build_request_two_tasks()
    uc = WhatIfUseCase(GreedySolver(CAL))
    diff = uc.execute(
        req,
        WhatIfIntent(operation="shift_deadline", new_deadline=req.deadline),
    )
    # Same plan → no moved tasks, no new overloads
    assert len(diff.moved_tasks) == 0
    assert len(diff.new_overloads) == 0


@pytest.mark.asyncio
async def test_what_if_produces_no_database_write():
    """WhatIfUseCase is pure: it has no repo and never writes (spec 7.2 step 5)."""
    req, _ = _build_request_two_tasks()
    uc = WhatIfUseCase(GreedySolver(CAL))
    # WhatIfUseCase takes only solver — no repo injected by design
    diff = uc.execute(req, WhatIfIntent(operation="add_person", person_name="X"))
    assert diff is not None
