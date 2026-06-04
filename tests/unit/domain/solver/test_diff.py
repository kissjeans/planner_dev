"""Unit tests for the what-if plan diff."""

from datetime import date
from uuid import uuid4

from planner.domain.models import Assignment, PlanResult, RiskFlag
from planner.domain.solver.diff import diff


def _assignment(task_id, start, end):
    return Assignment(task_id, uuid4(), start, end, allocations=())


def test_moved_task_detected():
    tid = uuid4()
    base = PlanResult(assignments=(_assignment(tid, date(2026, 6, 1), date(2026, 6, 1)),))
    modified = PlanResult(
        assignments=(_assignment(tid, date(2026, 6, 3), date(2026, 6, 3)),)
    )
    d = diff(base, modified)
    assert d.moved_tasks == (tid,)


def test_unchanged_task_not_moved():
    tid = uuid4()
    same = (_assignment(tid, date(2026, 6, 1), date(2026, 6, 1)),)
    d = diff(PlanResult(assignments=same), PlanResult(assignments=same))
    assert d.moved_tasks == ()


def test_overload_delta():
    pid = uuid4()
    day = date(2026, 6, 2)
    over = RiskFlag(kind="overload", message="x", person_id=pid, day=day)
    base = PlanResult(assignments=(), risks=())
    modified = PlanResult(assignments=(), risks=(over,))
    d = diff(base, modified)
    assert d.new_overloads == (over,)
    assert d.removed_overloads == ()
    # reverse direction removes it
    d2 = diff(modified, base)
    assert d2.removed_overloads == (over,)
    assert d2.new_overloads == ()
