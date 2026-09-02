"""Backward / no-deadline mode: earliest critical-path finish (spec 5.2).

Capacity-light: ignores resource contention and returns the longest
dependency chain expressed in working days (max earliest-finish over the DAG).
"""

from __future__ import annotations

import math
from datetime import date
from uuid import UUID

import networkx as nx

from planner.domain.calendar.ports import WorkingCalendar
from planner.domain.calendar.rules import first_working_day, nth_working_day
from planner.domain.models import Person, PlanRequest, Task
from planner.domain.units import DAY_HOURS

# Backward mode (spec §7): with no deadline the presented earliest date is the
# raw critical-path finish plus a safety buffer of this many working days.
# One day, not two: the customer's target is 6 working days of work + 1 buffer
# = 7 days from brief to sending (docs/customer-update-2026-08.md §3).
BACKWARD_BUFFER_WORKING_DAYS = 1


def _duration_days(task: Task, people_by_id: dict[UUID, Person]) -> float:
    """Length of a task in working days, kept fractional on purpose.

    The team plans in hours inside a day, so a one-hour task is 1/8 of a day,
    not a whole one. Rounding each task up to a day made a chain of ten short
    tasks read as ten days when the solver schedules them in two.
    """
    if task.duration_is_window:
        # Fixed calendar window (external resource): the span is nominal days,
        # not capacity-derived. An external has capacity_h=0, which would
        # otherwise clamp to 1 h/day and stretch a 2-day window into 16 days.
        return float(max(1, math.ceil(task.duration_hours / DAY_HOURS)))
    caps = [
        people_by_id[pid].capacity_h
        for pid in task.allowed_person_ids
        if pid in people_by_id
    ]
    cap = max(min(caps) if caps else DAY_HOURS, 1)
    return task.duration_hours / cap


def critical_path_end(
    req: PlanRequest, start: date, calendar: WorkingCalendar
) -> date:
    """Return the earliest finish date of the longest dependency chain."""
    g: nx.DiGraph = nx.DiGraph()
    for t in req.tasks:
        g.add_node(t.id)
    for d in req.dependencies:
        g.add_edge(d.depends_on_id, d.task_id, lag=d.lag_working_days)

    people_by_id: dict[UUID, Person] = {p.id: p for p in req.people}
    tasks_by_id: dict[UUID, Task] = {t.id: t for t in req.tasks}

    ef_days: dict[UUID, float] = {}
    max_ef = 0.0
    for tid in nx.topological_sort(g):
        dd = _duration_days(tasks_by_id[tid], people_by_id)
        # A positive lag (e.g. the FS+5 client-feedback wait) delays the
        # successor by that many working days — the greedy solver honours it,
        # so the critical path must too.
        base = max(
            (ef_days[p] + g.edges[p, tid].get("lag", 0) for p in g.predecessors(tid)),
            default=0,
        )
        ef = base + dd
        ef_days[tid] = ef
        max_ef = max(max_ef, ef)

    whole_days = math.ceil(max_ef)
    if whole_days <= 0:
        return first_working_day(calendar, start)
    return nth_working_day(calendar, start, whole_days)


def presented_earliest_end(
    req: PlanRequest, start: date, calendar: WorkingCalendar
) -> date:
    """Backward-mode date shown to the manager: raw finish + buffer (spec §7).

    Reuses ``next_working_day`` so the buffer lands on real working days
    (skipping weekends/holidays).
    """
    end = critical_path_end(req, start, calendar)
    for _ in range(BACKWARD_BUFFER_WORKING_DAYS):
        end = calendar.next_working_day(end)
    return end
