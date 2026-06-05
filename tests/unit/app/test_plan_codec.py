"""Round-trip tests for plan serialize/deserialize (spec 7.1 / 7.4)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from planner.app.add_project import (
    deserialize_allocations,
    deserialize_plan,
    serialize_plan,
)
from planner.domain.models import Assignment, DayAllocation, PlanResult, RiskFlag


def _plan() -> PlanResult:
    t, p = uuid4(), uuid4()
    return PlanResult(
        assignments=(
            Assignment(
                task_id=t,
                person_id=p,
                start_date=date(2026, 6, 8),
                end_date=date(2026, 6, 9),
                allocations=(
                    DayAllocation(p, date(2026, 6, 8), 8),
                    DayAllocation(p, date(2026, 6, 9), 4),
                ),
            ),
        ),
        risks=(
            RiskFlag(kind="overload", message="10ч", person_id=p, day=date(2026, 6, 8)),
        ),
        end_date=date(2026, 6, 9),
    )


def test_serialize_deserialize_round_trip():
    plan = _plan()
    assert deserialize_plan(serialize_plan(plan)) == plan


def test_deserialize_allocations_flattens_days():
    allocs = deserialize_allocations(serialize_plan(_plan()))
    assert len(allocs) == 2
    assert {a.hours for a in allocs} == {8, 4}


def test_deserialize_empty_payload():
    assert deserialize_plan({}) == PlanResult(assignments=())
    assert deserialize_allocations({}) == ()
