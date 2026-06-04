"""What-if diff between two plans (spec section 5.2 / 14)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from planner.domain.models import PlanDiff, PlanResult, RiskFlag


def _overload_keys(plan: PlanResult) -> set[tuple[UUID | None, date | None]]:
    return {(r.person_id, r.day) for r in plan.overloads()}


def diff(base: PlanResult, modified: PlanResult) -> PlanDiff:
    """Report moved tasks and the overload delta between two plans."""
    b = base.by_task()
    moved = tuple(
        a.task_id
        for a in modified.assignments
        if (a.task_id not in b)
        or b[a.task_id].start_date != a.start_date
        or b[a.task_id].end_date != a.end_date
    )

    base_keys = _overload_keys(base)
    mod_keys = _overload_keys(modified)
    new_overloads: tuple[RiskFlag, ...] = tuple(
        r for r in modified.overloads() if (r.person_id, r.day) not in base_keys
    )
    removed_overloads: tuple[RiskFlag, ...] = tuple(
        r for r in base.overloads() if (r.person_id, r.day) not in mod_keys
    )
    return PlanDiff(
        moved_tasks=moved,
        new_overloads=new_overloads,
        removed_overloads=removed_overloads,
    )
