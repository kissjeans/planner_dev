"""Immutable domain dataclasses used by the solver.

These are pure value objects (no IO, no ORM). The solver consumes a
``PlanRequest`` and returns a ``PlanResult``. Mapping to/from SQLAlchemy
models lives in ``infra/db``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

# Hard binding strictness levels (see spec section 4.1 / TZ section 7).
Strictness = str  # 'A' | 'B' | 'C'
LinkType = str  # 'FS' | 'SS'


@dataclass(frozen=True)
class Person:
    """A team member with a daily hour capacity."""

    id: UUID
    name: str
    capacity_h: int = 8


@dataclass(frozen=True)
class Task:
    """A schedulable unit of work.

    ``allowed_person_ids`` is the hard executor binding. A ``done`` task with
    ``fixed_start`` / ``fixed_assignee_id`` is treated as immovable.
    """

    id: UUID
    name: str
    duration_hours: int
    allowed_person_ids: tuple[UUID, ...]
    project_id: UUID | None = None
    required_skills: tuple[str, ...] = ()  # LLM-inferred skill hints (spec 3)
    is_splittable: bool = False
    allow_two_assignees: bool = False
    status: str = "not_done"  # not_done / done / preliminary / confirmed
    source: str = "bot_formed"  # 'bot_formed' | 'template' (provenance, spec 4)
    fixed_start: date | None = None
    fixed_end: date | None = None
    fixed_assignee_id: UUID | None = None


@dataclass(frozen=True)
class Dependency:
    """An edge ``depends_on_id`` -> ``task_id`` of a given link type."""

    task_id: UUID
    depends_on_id: UUID
    link_type: LinkType = "FS"


@dataclass(frozen=True)
class DayOverride:
    """Per-person per-day capacity override (0 = full day off / vacation)."""

    person_id: UUID
    day: date
    capacity_h: int


@dataclass(frozen=True)
class DayAllocation:
    """A chunk of one task's hours done by a person on a single day."""

    person_id: UUID
    day: date
    hours: int


@dataclass(frozen=True)
class Assignment:
    """The scheduled placement of one task."""

    task_id: UUID
    person_id: UUID
    start_date: date
    end_date: date
    allocations: tuple[DayAllocation, ...]


@dataclass(frozen=True)
class RiskFlag:
    """A soft signal (overload) or hard signal (deadline missed)."""

    kind: str  # 'overload' | 'deadline_missed'
    message: str
    task_id: UUID | None = None
    person_id: UUID | None = None
    day: date | None = None


@dataclass(frozen=True)
class PlanRequest:
    """Everything the solver needs to produce a schedule."""

    people: tuple[Person, ...]
    tasks: tuple[Task, ...]
    dependencies: tuple[Dependency, ...]
    horizon_start: date
    day_overrides: tuple[DayOverride, ...] = ()
    existing_allocations: tuple[DayAllocation, ...] = ()
    deadline: date | None = None


@dataclass(frozen=True)
class PlanResult:
    """The solver's output: placements plus risk signals."""

    assignments: tuple[Assignment, ...]
    risks: tuple[RiskFlag, ...] = ()
    end_date: date | None = None

    def by_task(self) -> dict[UUID, Assignment]:
        return {a.task_id: a for a in self.assignments}

    def overloads(self) -> tuple[RiskFlag, ...]:
        return tuple(r for r in self.risks if r.kind == "overload")


@dataclass(frozen=True)
class PlanDiff:
    """The delta between two plans (used by the 'what-if' use-case)."""

    moved_tasks: tuple[UUID, ...] = ()
    new_overloads: tuple[RiskFlag, ...] = ()
    removed_overloads: tuple[RiskFlag, ...] = ()
