"""WhatIfUseCase (spec section 7.2 / 14).

Clones the base plan request in memory, applies the operation, re-solves, and
returns the diff. Never writes to the DB (spec: PlanVersion is NOT persisted).
"""

from __future__ import annotations

import dataclasses
from uuid import uuid4

from planner.domain.intent import WhatIfIntent
from planner.domain.models import PlanDiff, PlanRequest, Person, Task
from planner.domain.solver.ports import SolverPort


def apply_operation(req: PlanRequest, intent: WhatIfIntent) -> PlanRequest:
    """Return a new ``PlanRequest`` with the what-if operation applied."""
    if intent.operation == "shift_deadline":
        return dataclasses.replace(req, deadline=intent.new_deadline or req.deadline)

    if intent.operation == "add_person":
        extra = Person(id=uuid4(), name=intent.person_name or "Доп. ресурс")
        people = req.people + (extra,)
        # Broaden executor binding so the new person can pick up any task.
        tasks = tuple(
            dataclasses.replace(
                t, allowed_person_ids=t.allowed_person_ids + (extra.id,)
            )
            for t in req.tasks
        )
        return dataclasses.replace(req, people=people, tasks=tasks)

    if intent.operation == "drop_project":
        return dataclasses.replace(req, tasks=(), dependencies=())

    # switch_to_lite needs template metadata not present in the solver request;
    # MVP treats it as a no-op here and lets the caller rebuild from the lite
    # template (spec section 6, deferred detail).
    return req


class WhatIfUseCase:
    def __init__(self, solver: SolverPort) -> None:
        self._solver = solver

    def execute(self, base_request: PlanRequest, intent: WhatIfIntent) -> PlanDiff:
        base = self._solver.plan(base_request)
        modified = self._solver.plan(apply_operation(base_request, intent))
        return self._solver.diff(base, modified)
