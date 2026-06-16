"""Acceptance-level unit tests for the greedy solver (spec section 13, Sprint 2)."""

from datetime import date
from uuid import uuid4

import networkx as nx
import pytest

from planner.domain.calendar.rules import WeekendCalendar, nth_working_day
from planner.domain.models import (
    DayOverride,
    Dependency,
    Person,
    PlanRequest,
    Task,
)
from planner.domain.solver.greedy import GreedySolver

START = date(2026, 6, 1)  # Monday
CAL = WeekendCalendar()


def _person(name="P", cap=8):
    return Person(id=uuid4(), name=name, capacity_h=cap)


def _task(person_ids, hours=8, splittable=False, name="T"):
    return Task(
        id=uuid4(),
        name=name,
        duration_hours=hours,
        allowed_person_ids=tuple(person_ids),
        is_splittable=splittable,
    )


def _solve(people, tasks, deps=(), **kw):
    req = PlanRequest(
        people=tuple(people),
        tasks=tuple(tasks),
        dependencies=tuple(deps),
        horizon_start=START,
        **kw,
    )
    return GreedySolver(CAL).plan(req)


def test_linear_chain_five_fs_tasks_one_person():
    p = _person()
    tasks = [_task([p.id], 8, name=f"t{i}") for i in range(5)]
    deps = [Dependency(tasks[i].id, tasks[i - 1].id, "FS") for i in range(1, 5)]
    res = _solve([p], tasks, deps)
    assert res.end_date == nth_working_day(CAL, START, 5)
    # each task starts strictly after its predecessor ends
    by = res.by_task()
    for i in range(1, 5):
        assert by[tasks[i].id].start_date > by[tasks[i - 1].id].end_date


def test_cycle_raises():
    p = _person()
    a, b = _task([p.id]), _task([p.id])
    deps = [Dependency(a.id, b.id, "FS"), Dependency(b.id, a.id, "FS")]
    with pytest.raises(nx.NetworkXUnfeasible):
        _solve([p], [a, b], deps)


def test_splittable_16h_capacity_8_spans_two_days():
    p = _person(cap=8)
    t = _task([p.id], hours=16, splittable=True)
    res = _solve([p], [t])
    a = res.by_task()[t.id]
    assert len(a.allocations) == 2
    assert {al.hours for al in a.allocations} == {8}
    assert a.end_date == nth_working_day(CAL, START, 2)
    assert res.overloads() == ()


def test_vacation_shifts_task():
    p = _person(cap=8)
    t = _task([p.id], hours=8)
    override = DayOverride(person_id=p.id, day=START, capacity_h=0)
    res = _solve([p], [t], day_overrides=(override,))
    a = res.by_task()[t.id]
    assert a.start_date == CAL.next_working_day(START)


def test_two_parallel_projects_no_conflict():
    p1, p2 = _person("A"), _person("B")
    t1, t2 = _task([p1.id]), _task([p2.id])
    res = _solve([p1, p2], [t1, t2])
    assert res.overloads() == ()
    assert res.by_task()[t1.id].start_date == res.by_task()[t2.id].start_date == START


def test_two_tasks_one_person_queue_no_overload():
    p = _person(cap=8)
    t1, t2 = _task([p.id], 8), _task([p.id], 8)
    res = _solve([p], [t1, t2])
    a1, a2 = res.by_task()[t1.id], res.by_task()[t2.id]
    assert a1.start_date != a2.start_date
    assert res.overloads() == ()


def test_done_task_is_not_moved():
    p = _person()
    fixed = date(2026, 5, 20)
    t = Task(
        id=uuid4(),
        name="done",
        duration_hours=8,
        allowed_person_ids=(p.id,),
        status="done",
        fixed_start=fixed,
        fixed_end=fixed,
        fixed_assignee_id=p.id,
    )
    res = _solve([p], [t])
    assert res.by_task()[t.id].start_date == fixed


def test_overload_flagged_when_duration_exceeds_capacity():
    p = _person(cap=8)
    t = _task([p.id], hours=16, splittable=False)
    res = _solve([p], [t])
    assert any(r.kind == "overload" for r in res.risks)


def test_overload_message_is_in_days_not_hours():
    """Spec §6: user-facing overload text shows working days, not raw hours."""
    p = _person(cap=8)
    t = _task([p.id], hours=16, splittable=False)  # 16h on an 8h/day person → 2 days
    res = _solve([p], [t])
    overload = next(r for r in res.risks if r.kind == "overload")
    # No raw hour markers ("h"/"ч") leak into the user-facing message.
    assert "h " not in overload.message and "ч" not in overload.message
    assert "дн" in overload.message  # framed in working days
    assert "2" in overload.message  # 16h ÷ 8h/day = 2 working days of load


def test_deadline_missed_flag():
    p = _person(cap=8)
    tasks = [_task([p.id], 8, name=f"t{i}") for i in range(3)]
    deps = [Dependency(tasks[i].id, tasks[i - 1].id, "FS") for i in range(1, 3)]
    res = _solve([p], tasks, deps, deadline=START)  # 3-day chain vs same-day deadline
    assert any(r.kind == "deadline_missed" for r in res.risks)


def test_executor_binding_respected():
    a, b = _person("A"), _person("B")
    t = _task([b.id])  # only B allowed, even though A is free
    res = _solve([a, b], [t])
    assert res.by_task()[t.id].person_id == b.id


def test_orphaned_dep_node_skipped_and_successor_still_scheduled():
    """greedy.py:103+211 — dep references task_id not in req.tasks; solver skips orphan
    node and schedules the successor from horizon_start (a is None → continue)."""
    p = _person()
    orphan_id = uuid4()  # a task ID referenced in a dep but NOT in req.tasks
    t_follow = _task([p.id])
    dep = Dependency(task_id=t_follow.id, depends_on_id=orphan_id, link_type="FS")
    res = _solve([p], [t_follow], [dep])
    assert res.by_task().get(t_follow.id) is not None


def test_splittable_horizon_overload():
    """greedy.py:157-158 — horizon saturated, remainder dumped on start day."""
    from planner.domain.solver.greedy import CapacityIndex, _allocate_split

    p = _person(cap=8)
    t = _task([p.id], hours=16, splittable=True)
    # horizon_limit == START means loop runs 0 iterations → remaining > 0
    horizon_limit = START
    idx = CapacityIndex((p,), CAL, (), ())
    allocs, start, end = _allocate_split(t, p, START, CAL, idx, horizon_limit)
    assert len(allocs) >= 1  # overload alloc dumped on start day
