"""Repository port + records consumed by the use-cases (spec section 7).

The use-cases depend on this abstract async interface, not on SQLAlchemy.
The concrete adapter lives in ``infra/db/repo.py`` (single writer, spec 17);
tests supply a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from planner.domain.models import Assignment as DomainAssignment
from planner.domain.models import Task as DomainTask


@dataclass(frozen=True)
class SinkTask:
    title: str
    assignees: list[str]
    project: str | None
    deadline: date | None


class TaskSinkPort(Protocol):
    async def push_task(self, task: SinkTask) -> str | None:
        """Mirror a captured task to an external sink. Returns a URL/id or None."""
        ...


@dataclass(frozen=True)
class PersonRecord:
    id: UUID
    name: str
    is_admin: bool = False
    capacity_h: int = 8
    role_label: str | None = None


@dataclass(frozen=True)
class PlanVersionRecord:
    id: UUID
    project_id: UUID
    status: str  # 'proposed' | 'committed'
    payload: dict[str, Any]


@dataclass(frozen=True)
class ProjectRecord:
    id: UUID
    title: str
    status: str
    deadline: date | None = None
    priority: str = "medium"
    template_code: str | None = None
    start_date: date | None = None


@dataclass(frozen=True)
class TaskRecord:
    id: UUID
    name: str
    status: str
    start_date: date | None = None
    end_date: date | None = None
    duration_hours: int = 0


@dataclass(frozen=True)
class CapabilityRecord:
    """An active person and the union of skills granted by their roles (spec 4)."""

    person_id: UUID
    name: str
    skills: frozenset[str]
    is_external: bool = False


@dataclass(frozen=True)
class AuditRecord:
    created_at: str
    action: str
    entity_type: str
    actor_name: str | None = None
    payload: dict[str, Any] | None = None
    entity_id: UUID | None = None


@dataclass(frozen=True)
class TaskMeta:
    """A persisted task with its project + assignee, for the admin board."""

    task_id: UUID
    task_name: str
    project_title: str
    priority: str
    status: str
    start_date: date | None
    end_date: date | None
    duration_hours: int
    assignee_id: UUID | None
    assignee_name: str | None
    deadline: date | None


class RepoPort(Protocol):
    async def get_person_by_name(self, name: str) -> PersonRecord | None: ...

    async def get_plan_version(self, pv_id: UUID) -> PlanVersionRecord | None: ...

    async def set_plan_version_status(self, pv_id: UUID, status: str) -> None: ...

    async def transition_plan_status(
        self, pv_id: UUID, from_status: str, to_status: str
    ) -> bool: ...

    async def save_plan_version(
        self, project_id: UUID, status: str, payload: dict[str, Any], actor_id: UUID | None
    ) -> PlanVersionRecord: ...

    async def get_committed_plan(self, project_id: UUID) -> PlanVersionRecord | None: ...

    async def get_project_by_title(self, title: str) -> ProjectRecord | None:
        """Case-insensitive lookup of a project by title (for task capture)."""
        ...

    async def create_task(
        self,
        *,
        project_id: UUID,
        name: str,
        duration_hours: int,
        deadline: date | None,
        actor_id: UUID | None,
        required_skills: list[str] | None = None,
    ) -> TaskRecord:
        """Insert a standalone task (chat capture) and return it."""
        ...

    async def assign_task(self, task_id: UUID, person_id: UUID, hours: int) -> None:
        """Attach a person to a task (capture flow)."""
        ...

    async def list_committed_plans(self) -> list[dict[str, Any]]:
        """Payloads of all committed plan versions (for the load heatmap)."""
        ...

    async def list_task_dependencies(self) -> list[Any]:
        """All concrete task dependency edges (what-if baseline reconstruction)."""
        ...

    async def create_project(
        self,
        *,
        title: str,
        template_code: str,
        deadline: date | None,
        brief_return_date: date | None,
        actor_id: UUID | None,
        priority: str = "medium",
        project_id: UUID | None = None,
    ) -> ProjectRecord: ...

    async def list_committed_plans_with_project(
        self,
    ) -> list[tuple[UUID, dict[str, Any]]]:
        """(project_id, payload) for every committed plan — admin board source."""
        ...

    async def get_task_name_map(self) -> dict[UUID, str]:
        """All task ids → names, for labelling the schedule/calendar views."""
        ...

    async def list_tasks_with_meta(self) -> list[TaskMeta]:
        """Persisted tasks joined with project + assignee — admin board source."""
        ...

    async def set_task_assignee(
        self, task_id: UUID, person_id: UUID, hours: int = 8
    ) -> bool:
        """Replace a task's assignee. True if the task exists."""
        ...

    async def reassign_in_plan(self, task_id: UUID, new_person_id: UUID) -> bool:
        """Move a task to another person inside its committed plan. True if moved."""
        ...

    async def upsert_day_override(
        self, person_id: UUID, day: date, capacity_h: int, reason: str | None
    ) -> None: ...

    async def list_day_overrides(self) -> tuple[Any, ...]:
        """All per-person per-day capacity overrides as domain ``DayOverride``s."""
        ...

    async def add_audit(
        self,
        actor_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        payload: dict[str, Any] | None,
    ) -> None: ...

    # --- Read side, consumed by the web admin (spec section 9) ---

    async def list_projects(self) -> list[ProjectRecord]: ...

    async def list_project_tasks(self, project_id: UUID) -> list[TaskRecord]: ...

    async def list_people(self) -> list[PersonRecord]: ...

    async def list_audit(self, limit: int = 50, offset: int = 0) -> list[AuditRecord]: ...

    async def get_person_by_tg_id(self, tg_user_id: int) -> PersonRecord | None: ...

    # --- Capability matching (spec section 5) ---

    async def get_person_capabilities(self) -> tuple[CapabilityRecord, ...]:
        """Active people with the union of skills implied by their roles."""
        ...

    # --- Solver inputs (spec section 7.1) ---

    async def get_solver_people(self) -> tuple[Any, ...]:
        """Active team members as domain ``Person`` objects for the solver."""
        ...

    async def get_project_template(self, code: str) -> Any | None:
        """A ``ProjectTemplate`` (app.add_project) by code, or None if absent."""
        ...

    async def update_task_schedule(
        self, task_id: UUID, start: date | None, end: date | None, person_id: UUID | None
    ) -> None: ...

    async def set_task_status(self, task_id: UUID, status: str) -> None: ...

    async def set_project_status(self, project_id: UUID, status: str) -> None: ...

    async def save_project_tasks(
        self,
        project_id: UUID,
        tasks: tuple[DomainTask, ...],
        assignments: tuple[DomainAssignment, ...],
    ) -> None: ...
