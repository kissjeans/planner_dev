"""Admin board projections (client xlsx vision): Schedule, Calendar, Load.

All three tabs are projections of the persisted ``tasks`` table (+ assignee +
project). Captured tasks and solver tasks both live there, so a task shows up
the moment the bot writes it. Pure logic, no IO, fully unit-testable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from planner.app.ports import PersonRecord, TaskMeta
from planner.domain.slots import hours_to_slots

DEFAULT_DAYS = 14


@dataclass(frozen=True)
class ScheduleRow:
    task_id: str
    project_title: str
    task_name: str
    assignee_name: str
    assignee_id: str
    deadline: date | None
    slots: int
    status: str


@dataclass(frozen=True)
class CalendarRow:
    person_name: str
    cells: tuple[str, ...]  # task names that person has that day ("" when idle)


@dataclass(frozen=True)
class LoadRow:
    name: str
    capacity_slots: int
    slots: tuple[int, ...]
    overloaded: tuple[bool, ...]
    free: tuple[int, ...]
    pct: int  # window load: used slots / available slots, %


@dataclass(frozen=True)
class Board:
    days: tuple[date, ...]
    schedule: tuple[ScheduleRow, ...]
    calendar: tuple[CalendarRow, ...]
    load_rows: tuple[LoadRow, ...]
    totals: tuple[int, ...]  # total slots booked per day


def _task_span(task: TaskMeta) -> list[date]:
    """Calendar days a task occupies (inclusive). Falls back to the deadline."""
    start, end = task.start_date, task.end_date or task.deadline
    if start and end and end >= start:
        n = (end - start).days + 1
        return [start + timedelta(days=i) for i in range(n)]
    single = end or start
    return [single] if single else []


class AdminBoardUseCase:
    def build(
        self,
        *,
        tasks: list[TaskMeta],
        people: list[PersonRecord],
        start: date,
        days: int = DEFAULT_DAYS,
    ) -> Board:
        day_list = [start + timedelta(days=i) for i in range(days)]
        index = {d: i for i, d in enumerate(day_list)}

        person_day_h: dict[tuple[UUID, int], float] = defaultdict(float)
        cal_cells: dict[tuple[str, int], list[str]] = defaultdict(list)
        schedule: list[ScheduleRow] = []

        for t in tasks:
            schedule.append(
                ScheduleRow(
                    task_id=str(t.task_id),
                    project_title=t.project_title,
                    task_name=t.task_name,
                    assignee_name=t.assignee_name or "—",
                    assignee_id=str(t.assignee_id) if t.assignee_id else "",
                    deadline=t.deadline,
                    slots=hours_to_slots(t.duration_hours),
                    status=t.status,
                )
            )
            span = _task_span(t)
            per_day_h = t.duration_hours / len(span) if span else 0
            for d in span:
                if d not in index:
                    continue
                j = index[d]
                if t.assignee_name:
                    cal_cells[(t.assignee_name, j)].append(t.task_name)
                if t.assignee_id is not None:
                    person_day_h[(t.assignee_id, j)] += per_day_h

        schedule.sort(key=lambda r: (r.deadline or date.max, r.project_title, r.task_name))
        calendar = self._calendar(people, cal_cells, days)
        load_rows, totals = self._load(people, person_day_h, days)
        return Board(
            days=tuple(day_list),
            schedule=tuple(schedule),
            calendar=tuple(calendar),
            load_rows=tuple(load_rows),
            totals=tuple(totals),
        )

    @staticmethod
    def _calendar(
        people: list[PersonRecord],
        cal_cells: dict[tuple[str, int], list[str]],
        days: int,
    ) -> list[CalendarRow]:
        return [
            CalendarRow(
                person_name=p.name,
                cells=tuple(
                    ", ".join(cal_cells.get((p.name, j), [])) for j in range(days)
                ),
            )
            for p in people
        ]

    @staticmethod
    def _load(
        people: list[PersonRecord],
        person_day_h: dict[tuple[UUID, int], float],
        days: int,
    ) -> tuple[list[LoadRow], list[int]]:
        totals = [0] * days
        rows: list[LoadRow] = []
        for p in people:
            cap = hours_to_slots(p.capacity_h)
            slots = [
                hours_to_slots(person_day_h[(p.id, j)]) for j in range(days)
            ]
            for j, s in enumerate(slots):
                totals[j] += s
            free = [max(cap - s, 0) for s in slots]
            overloaded = [s > cap for s in slots]
            available = cap * days
            pct = round(sum(slots) / available * 100) if available else 0
            rows.append(
                LoadRow(
                    name=p.name, capacity_slots=cap, slots=tuple(slots),
                    overloaded=tuple(overloaded), free=tuple(free), pct=pct,
                )
            )
        return rows, totals
