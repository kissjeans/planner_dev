"""Tests for the /replan handler + summary (spec section 8).

/replan re-runs the solver over committed projects with current day_overrides
and reports a refreshed load/overload summary. It is read-only: it must never
write a plan version or mutate committed plans (spec: no silent overwrite).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from planner.app.add_project import serialize_plan
from planner.bot.handlers import replan
from planner.bot.handlers.replan import build_replan_summary
from planner.domain.calendar.rules import WeekendCalendar
from planner.domain.models import (
    Assignment,
    DayAllocation,
    DayOverride,
    Person,
    PlanResult,
    RiskFlag,
)
from planner.domain.solver.greedy import GreedySolver

TODAY = date(2026, 6, 8)  # Monday


class _ReplanRepo:
    def __init__(self) -> None:
        self.solver_people: tuple[Person, ...] = ()
        self.committed_payloads: list[dict] = []
        self.task_names: dict = {}
        self.dependencies: list = []
        self.overrides: tuple[DayOverride, ...] = ()
        self.writes: list = []

    async def get_solver_people(self):
        return self.solver_people

    async def list_committed_plans(self):
        return list(self.committed_payloads)

    async def get_task_name_map(self):
        return dict(self.task_names)

    async def get_task_project_map(self):
        return dict(getattr(self, "task_projects", {}))

    async def list_task_dependencies(self):
        return list(self.dependencies)

    async def list_day_overrides(self):
        return tuple(self.overrides)

    # Any write would be a bug — record it so tests can assert it never happens.
    async def save_plan_version(self, *a, **k):
        self.writes.append(("save_plan_version", a, k))

    async def transition_plan_status(self, *a, **k):
        self.writes.append(("transition_plan_status", a, k))
        return False


class _Answers:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, text: str, **kwargs: Any) -> None:
        self.calls.append(text)


def _message(text: str = "/replan") -> tuple[SimpleNamespace, _Answers]:
    answers = _Answers()
    return SimpleNamespace(text=text, answer=answers), answers


def _committed(person_id, task_id, day, hours):
    plan = PlanResult(
        assignments=(
            Assignment(
                task_id=task_id,
                person_id=person_id,
                start_date=day,
                end_date=day,
                allocations=(DayAllocation(person_id, day, hours),),
            ),
        )
    )
    return serialize_plan(plan)


def _solver() -> GreedySolver:
    return GreedySolver(WeekendCalendar())


@pytest.mark.asyncio
async def test_build_summary_no_committed_plans():
    repo = _ReplanRepo()
    repo.solver_people = (Person(id=uuid4(), name="Андрей"),)
    summary = await build_replan_summary(repo, _solver(), today=TODAY)
    assert "нет" in summary.lower()
    assert not repo.writes


class _CapturingSolver:
    """Records the request it is asked to solve and returns a canned result."""

    def __init__(self, result: PlanResult) -> None:
        self._result = result
        self.request = None

    def plan(self, request):
        self.request = request
        return self._result


@pytest.mark.asyncio
async def test_build_summary_passes_current_overrides_to_solver():
    repo = _ReplanRepo()
    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo.solver_people = (andrey,)
    task_id = uuid4()
    repo.task_names = {task_id: "Дизайн"}
    repo.committed_payloads = [_committed(andrey.id, task_id, TODAY, 8)]
    # A vacation entered since the last commit must feed into the re-solve.
    override = DayOverride(person_id=andrey.id, day=TODAY, capacity_h=0)
    repo.overrides = (override,)

    solver = _CapturingSolver(PlanResult(assignments=()))
    await build_replan_summary(repo, solver, today=TODAY)

    assert solver.request is not None
    assert override in solver.request.day_overrides
    assert not repo.writes  # read-only: never persists


@pytest.mark.asyncio
async def test_build_summary_reports_named_overloads():
    repo = _ReplanRepo()
    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo.solver_people = (andrey,)
    task_id = uuid4()
    repo.task_names = {task_id: "Дизайн"}
    repo.committed_payloads = [_committed(andrey.id, task_id, TODAY, 8)]

    # Solver reports an overload for Andrey on TODAY.
    overload = RiskFlag(
        kind="overload", message="16h vs 8h", person_id=andrey.id, day=TODAY
    )
    solver = _CapturingSolver(PlanResult(assignments=(), risks=(overload,)))
    summary = await build_replan_summary(repo, solver, today=TODAY)

    assert "Андрей" in summary
    assert not repo.writes


@pytest.mark.asyncio
async def test_build_summary_no_overloads_reports_clean():
    repo = _ReplanRepo()
    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo.solver_people = (andrey,)
    task_id = uuid4()
    repo.task_names = {task_id: "Дизайн"}
    repo.committed_payloads = [_committed(andrey.id, task_id, TODAY, 8)]

    summary = await build_replan_summary(repo, _solver(), today=TODAY)

    assert "перегруз" in summary.lower()
    assert not repo.writes


@pytest.mark.asyncio
async def test_handler_non_admin_blocked():
    repo = _ReplanRepo()
    msg, answers = _message()
    await replan.handle_replan(msg, {"is_admin": False}, repo=repo, solver=_solver())
    assert "админ" in answers.calls[0].lower()
    assert not repo.writes


@pytest.mark.asyncio
async def test_handler_no_repo_degrades():
    msg, answers = _message()
    await replan.handle_replan(msg, {"is_admin": True}, repo=None, solver=None)
    assert answers.calls


@pytest.mark.asyncio
async def test_handler_admin_replies_with_summary():
    repo = _ReplanRepo()
    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo.solver_people = (andrey,)
    task_id = uuid4()
    repo.task_names = {task_id: "Дизайн"}
    repo.committed_payloads = [_committed(andrey.id, task_id, TODAY, 8)]

    msg, answers = _message()
    await replan.handle_replan(msg, {"is_admin": True}, repo=repo, solver=_solver())

    assert answers.calls
    assert not repo.writes
