"""Backward / no-deadline mode: earliest critical-path finish (spec 5.2).

Capacity-light: ignores resource contention and returns the longest
dependency chain expressed in working days (max earliest-finish over the DAG).
"""

from __future__ import annotations

import math
from datetime import date

import networkx as nx

from uuid import UUID

from planner.domain.calendar.ports import WorkingCalendar
from planner.domain.calendar.rules import first_working_day, nth_working_day
from planner.domain.models import PlanRequest, Person, Task


def _duration_days(task: Task, people_by_id: dict[UUID, Person]) -> int:
    caps = [
        people_by_id[pid].capacity_h
        for pid in task.allowed_person_ids
        if pid in people_by_id
    ]
    cap = max(min(caps) if caps else 8, 1)
    return max(1, math.ceil(task.duration_hours / cap))


def critical_path_end(
    req: PlanRequest, start: date, calendar: WorkingCalendar
) -> date:
    """Return the earliest finish date of the longest dependency chain."""
    g: nx.DiGraph = nx.DiGraph()
    for t in req.tasks:
        g.add_node(t.id)
    for d in req.dependencies:
        g.add_edge(d.depends_on_id, d.task_id)

    people_by_id: dict[UUID, Person] = {p.id: p for p in req.people}
    tasks_by_id: dict[UUID, Task] = {t.id: t for t in req.tasks}

    ef_days: dict[UUID, int] = {}
    max_ef = 0
    for tid in nx.topological_sort(g):
        dd = _duration_days(tasks_by_id[tid], people_by_id)
        base = max((ef_days[p] for p in g.predecessors(tid)), default=0)
        ef = base + dd
        ef_days[tid] = ef
        max_ef = max(max_ef, ef)

    if max_ef == 0:
        return first_working_day(calendar, start)
    return nth_working_day(calendar, start, max_ef)
