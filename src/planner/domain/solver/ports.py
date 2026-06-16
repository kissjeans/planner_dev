"""Solver port (spec section 5.1)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from planner.domain.models import PlanDiff, PlanRequest, PlanResult


class SolverPort(Protocol):
    """Swappable scheduling strategy (greedy MVP, PyJobShop in v2)."""

    def plan(self, req: PlanRequest) -> PlanResult: ...

    def critical_path_end(self, req: PlanRequest, start: date) -> date: ...

    def presented_earliest_end(self, req: PlanRequest, start: date) -> date: ...

    def diff(self, base: PlanResult, modified: PlanResult) -> PlanDiff: ...
