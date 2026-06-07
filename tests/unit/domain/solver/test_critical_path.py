"""Unit tests for backward / critical-path mode."""

from datetime import date
from uuid import uuid4

from planner.domain.calendar.rules import WeekendCalendar, nth_working_day
from planner.domain.models import Dependency, Person, PlanRequest, Task
from planner.domain.solver.greedy import GreedySolver

START = date(2026, 6, 1)
CAL = WeekendCalendar()


def _task(pid, hours=8, name="t"):
    return Task(id=uuid4(), name=name, duration_hours=hours, allowed_person_ids=(pid,))


def _end(people, tasks, deps):
    req = PlanRequest(
        people=tuple(people),
        tasks=tuple(tasks),
        dependencies=tuple(deps),
        horizon_start=START,
    )
    return GreedySolver(CAL).critical_path_end(req, START)


def test_chain_critical_path_is_sum_of_durations():
    p = Person(id=uuid4(), name="P", capacity_h=8)
    tasks = [_task(p.id, 8, f"t{i}") for i in range(5)]
    deps = [Dependency(tasks[i].id, tasks[i - 1].id, "FS") for i in range(1, 5)]
    assert _end([p], tasks, deps) == nth_working_day(CAL, START, 5)


def test_independent_tasks_critical_path_is_one_day():
    p = Person(id=uuid4(), name="P", capacity_h=8)
    tasks = [_task(p.id, 8, f"t{i}") for i in range(3)]
    assert _end([p], tasks, []) == nth_working_day(CAL, START, 1)


def test_empty_task_list_returns_first_working_day():
    """max_ef == 0 branch (line 53): no tasks → returns first working day."""
    from planner.domain.solver.critical_path import critical_path_end
    p = Person(id=uuid4(), name="P", capacity_h=8)
    req = PlanRequest(people=(p,), tasks=(), dependencies=(), horizon_start=START)
    result = critical_path_end(req, START, CAL)
    from planner.domain.calendar.rules import first_working_day
    assert result == first_working_day(CAL, START)
