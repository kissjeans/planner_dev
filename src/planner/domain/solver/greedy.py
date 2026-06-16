"""Greedy forward scheduler on a NetworkX DAG (spec section 5.2).

Strategy: topologically sort tasks, then place each at the earliest working
slot that satisfies its dependencies and the chosen executor's capacity.
Mild and capacity-light: no global optimisation (explicit MVP trade-off,
spec section 5.3). Overloads are emitted as soft signals, never blockers.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID

import networkx as nx

from planner.domain.calendar.ports import WorkingCalendar
from planner.domain.calendar.rules import first_working_day
from planner.domain.models import (
    Assignment,
    DayAllocation,
    DayOverride,
    Dependency,
    Person,
    PlanDiff,
    PlanRequest,
    PlanResult,
    RiskFlag,
    Task,
)
from planner.domain.solver.critical_path import critical_path_end as _critical_path_end
from planner.domain.solver.critical_path import (
    presented_earliest_end as _presented_earliest_end,
)
from planner.domain.solver.diff import diff as _diff
from planner.domain.units import hours_to_working_days

# Planning horizon: how far ahead the greedy search is allowed to look.
HORIZON_DAYS = 365


class CapacityIndex:
    """Tracks used hours per (person, day) and answers remaining capacity."""

    def __init__(
        self,
        people: tuple[Person, ...],
        calendar: WorkingCalendar,
        day_overrides: tuple[DayOverride, ...],
        existing: tuple[DayAllocation, ...],
    ) -> None:
        self._cap = {p.id: p.capacity_h for p in people}
        self._cal = calendar
        self._override = {(o.person_id, o.day): o.capacity_h for o in day_overrides}
        self._used: dict[tuple[UUID, date], int] = defaultdict(int)
        for a in existing:
            self._used[(a.person_id, a.day)] += a.hours

    def base_capacity(self, person_id: UUID, day: date) -> int:
        """Capacity ceiling for a day; overrides win, weekends are 0."""
        override = self._override.get((person_id, day))
        if override is not None:
            return override
        if not self._cal.is_working_day(day):
            return 0
        return self._cap.get(person_id, 0)

    def remaining(self, person_id: UUID, day: date) -> int:
        return self.base_capacity(person_id, day) - self._used[(person_id, day)]

    def occupy(self, allocations: tuple[DayAllocation, ...]) -> None:
        for a in allocations:
            self._used[(a.person_id, a.day)] += a.hours

    def overloads(self) -> list[tuple[UUID, date, int, int]]:
        """Return (person_id, day, used, base) where used exceeds base."""
        out = []
        for (pid, day), used in self._used.items():
            base = self.base_capacity(pid, day)
            if used > base:
                out.append((pid, day, used, base))
        return out


def build_dag(tasks: list[Task], deps: list[Dependency]) -> nx.DiGraph:
    """Node = task_id; edge depends_on -> task carrying ``link_type``."""
    g = nx.DiGraph()
    for t in tasks:
        g.add_node(t.id)
    for d in deps:
        g.add_edge(d.depends_on_id, d.task_id, link_type=d.link_type)
    return g


def _earliest_start(
    task: Task,
    graph: nx.DiGraph,
    assignments: dict[UUID, Assignment],
    horizon_start: date,
    calendar: WorkingCalendar,
) -> date:
    """Raise the start past resolved dependencies (FS: after end; SS: at start)."""
    est = horizon_start
    for dep_id in graph.predecessors(task.id):
        a = assignments.get(dep_id)
        if a is None:
            continue
        link = graph.edges[dep_id, task.id]["link_type"]
        cand = calendar.next_working_day(a.end_date) if link == "FS" else a.start_date
        if cand > est:
            est = cand
    return est


def _allocate_single(
    task: Task,
    person: Person,
    earliest: date,
    calendar: WorkingCalendar,
    idx: CapacityIndex,
    horizon_limit: date,
) -> tuple[tuple[DayAllocation, ...], date, date]:
    """Place a non-splittable task on the earliest day that fits (one allocation)."""
    day = first_working_day(calendar, earliest)
    placed = None
    while day <= horizon_limit:
        if calendar.is_working_day(day) and idx.remaining(person.id, day) >= task.duration_hours:
            placed = day
            break
        day += timedelta(days=1)
    if placed is None:
        # Never fits (duration exceeds capacity, or horizon saturated): dump on
        # the earliest working day and let the overload scan flag it.
        placed = first_working_day(calendar, earliest)
    allocs = (DayAllocation(person.id, placed, task.duration_hours),)
    return allocs, placed, placed


def _allocate_split(
    task: Task,
    person: Person,
    earliest: date,
    calendar: WorkingCalendar,
    idx: CapacityIndex,
    horizon_limit: date,
) -> tuple[tuple[DayAllocation, ...], date, date]:
    """Spread a splittable task across consecutive working days by capacity."""
    remaining = task.duration_hours
    day = first_working_day(calendar, earliest)
    allocs: list[DayAllocation] = []
    while remaining > 0 and day <= horizon_limit:
        if calendar.is_working_day(day):
            cap = idx.remaining(person.id, day)
            if cap > 0:
                take = min(cap, remaining)
                allocs.append(DayAllocation(person.id, day, take))
                remaining -= take
        day += timedelta(days=1)
    if remaining > 0:
        # Horizon saturated: dump the remainder on the start day (overload).
        start = first_working_day(calendar, earliest)
        allocs.append(DayAllocation(person.id, start, remaining))
    start = min(a.day for a in allocs)
    end = max(a.day for a in allocs)
    return tuple(allocs), start, end


def _allocate(
    task: Task,
    person: Person,
    earliest: date,
    calendar: WorkingCalendar,
    idx: CapacityIndex,
    horizon_limit: date,
) -> tuple[tuple[DayAllocation, ...], date, date]:
    if task.is_splittable:
        return _allocate_split(task, person, earliest, calendar, idx, horizon_limit)
    return _allocate_single(task, person, earliest, calendar, idx, horizon_limit)


class GreedySolver:
    """Forward greedy scheduler. Implements :class:`SolverPort`."""

    def __init__(self, calendar: WorkingCalendar) -> None:
        self.calendar = calendar

    def plan(self, req: PlanRequest) -> PlanResult:
        graph = build_dag(list(req.tasks), list(req.dependencies))
        # Raises networkx.NetworkXUnfeasible on a cycle (spec acceptance).
        order = list(nx.topological_sort(graph))

        tasks_by_id = {t.id: t for t in req.tasks}
        people_by_id = {p.id: p for p in req.people}
        idx = CapacityIndex(
            req.people, self.calendar, req.day_overrides, req.existing_allocations
        )
        horizon_limit = req.horizon_start + timedelta(days=HORIZON_DAYS)

        assignments: dict[UUID, Assignment] = {}
        # Pre-place immovable (done) tasks so they occupy capacity but never move.
        for t in req.tasks:
            if t.status == "done" and t.fixed_start and t.fixed_assignee_id:
                end = t.fixed_end or t.fixed_start
                fixed_allocs: tuple[DayAllocation, ...] = (
                    DayAllocation(t.fixed_assignee_id, t.fixed_start, t.duration_hours),
                )
                idx.occupy(fixed_allocs)
                assignments[t.id] = Assignment(
                    t.id, t.fixed_assignee_id, t.fixed_start, end, fixed_allocs
                )

        for tid in order:
            if tid in assignments:
                continue
            if tid not in tasks_by_id:
                continue  # orphaned dep node — not a real task
            task = tasks_by_id[tid]
            earliest = _earliest_start(
                task, graph, assignments, req.horizon_start, self.calendar
            )
            allowed = [
                people_by_id[pid]
                for pid in task.allowed_person_ids
                if pid in people_by_id
            ] or list(req.people)

            best: tuple[tuple[DayAllocation, ...], date, date, Person] | None = None
            for person in allowed:
                allocs, start, end = _allocate(
                    task, person, earliest, self.calendar, idx, horizon_limit
                )
                if best is None or end < best[2]:
                    best = (allocs, start, end, person)

            assert best is not None
            allocs, start, end, person = best
            idx.occupy(allocs)
            assignments[tid] = Assignment(tid, person.id, start, end, allocs)

        risks = _overload_flags(idx)
        end_date = max((a.end_date for a in assignments.values()), default=None)
        if req.deadline and end_date and end_date > req.deadline:
            risks.append(
                RiskFlag(
                    kind="deadline_missed",
                    message=f"Plan ends {end_date}, deadline {req.deadline}.",
                )
            )
        ordered = tuple(assignments[tid] for tid in order if tid in assignments)
        return PlanResult(assignments=ordered, risks=tuple(risks), end_date=end_date)

    def critical_path_end(self, req: PlanRequest, start: date) -> date:
        return _critical_path_end(req, start, self.calendar)

    def presented_earliest_end(self, req: PlanRequest, start: date) -> date:
        return _presented_earliest_end(req, start, self.calendar)

    def diff(self, base: PlanResult, modified: PlanResult) -> PlanDiff:
        return _diff(base, modified)


def _overload_flags(idx: CapacityIndex) -> list[RiskFlag]:
    flags = []
    for pid, day, used, base in idx.overloads():
        # Internal math stays in hours; the user-facing message is in working
        # days (spec §6): the day's capacity is the 1-day norm.
        used_days = hours_to_working_days(used, base)
        flags.append(
            RiskFlag(
                kind="overload",
                message=f"≈{used_days} раб. дн. нагрузки на 1 день, {day}.",
                person_id=pid,
                day=day,
            )
        )
    return flags
