"""AddProjectUseCase (spec section 7.1).

Instantiates a project's tasks from a template, runs the solver to produce a
proposed plan, persists it as a *proposed* ``PlanVersion``, and returns the
result for rendering in the bot/admin. Backward mode (no deadline) additionally
reports the critical-path end date (spec section 9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import networkx as nx

from planner.app.ports import PersonRecord, ProjectRecord, RepoPort
from planner.domain.intent import AddProjectIntent
from planner.domain.models import (
    Assignment,
    DayAllocation,
    DayOverride,
    Dependency,
    Person,
    PlanRequest,
    PlanResult,
    RiskFlag,
    Task,
)
from planner.domain.solver.ports import SolverPort


class InvalidProjectError(ValueError):
    """Raised when the intent fails domain validation (spec 7.1 step 1)."""


@dataclass(frozen=True)
class TemplateTaskSpec:
    """A template task definition the use-case deep-copies into a project."""

    ord: int
    name: str
    duration_hours: int
    allowed_person_ids: tuple[UUID, ...]
    depends_on_ords: tuple[int, ...] = ()
    link_types: tuple[str, ...] = ()  # parallel to depends_on_ords; default 'FS'
    is_splittable: bool = False
    allow_two_assignees: bool = False


@dataclass(frozen=True)
class ProjectTemplate:
    code: str
    tasks: tuple[TemplateTaskSpec, ...]


@dataclass(frozen=True)
class AddProjectResult:
    project: ProjectRecord
    plan_version_id: UUID
    plan: PlanResult
    tasks: tuple[Task, ...]  # instantiated tasks (for name maps / rendering)
    earliest_end: date | None  # backward-mode critical-path end + buffer (None in forward mode)


def instantiate_template(
    template: ProjectTemplate, project_id: UUID
) -> tuple[tuple[Task, ...], tuple[Dependency, ...]]:
    """Deep-copy template task specs into concrete domain tasks (fresh UUIDs).

    Template ``ord`` numbers are remapped to fresh task UUIDs so two projects
    from the same template never share task identities (spec 7.1 step 3).
    """
    ord_to_id: dict[int, UUID] = {spec.ord: uuid4() for spec in template.tasks}
    tasks = tuple(
        Task(
            id=ord_to_id[spec.ord],
            name=spec.name,
            duration_hours=spec.duration_hours,
            allowed_person_ids=spec.allowed_person_ids,
            project_id=project_id,
            is_splittable=spec.is_splittable,
            allow_two_assignees=spec.allow_two_assignees,
            source="template",
        )
        for spec in template.tasks
    )
    deps: list[Dependency] = []
    for spec in template.tasks:
        for i, dep_ord in enumerate(spec.depends_on_ords):
            link = spec.link_types[i] if i < len(spec.link_types) else "FS"
            deps.append(
                Dependency(
                    task_id=ord_to_id[spec.ord],
                    depends_on_id=ord_to_id[dep_ord],
                    link_type=link,
                )
            )
    return tasks, tuple(deps)


def serialize_plan(plan: PlanResult) -> dict[str, Any]:
    """JSON-safe snapshot of a plan for the ``plan_versions.payload`` column."""
    return {
        "assignments": [
            {
                "task_id": str(a.task_id),
                "person_id": str(a.person_id),
                "start_date": a.start_date.isoformat(),
                "end_date": a.end_date.isoformat(),
                "allocations": [
                    {
                        "person_id": str(al.person_id),
                        "day": al.day.isoformat(),
                        "hours": al.hours,
                    }
                    for al in a.allocations
                ],
            }
            for a in plan.assignments
        ],
        "risks": [
            {
                "kind": r.kind,
                "message": r.message,
                "task_id": str(r.task_id) if r.task_id else None,
                "person_id": str(r.person_id) if r.person_id else None,
                "day": r.day.isoformat() if r.day else None,
            }
            for r in plan.risks
        ],
        "end_date": plan.end_date.isoformat() if plan.end_date else None,
    }


def _opt_uuid(v: str | None) -> UUID | None:
    return UUID(v) if v else None


def _opt_date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def deserialize_allocations(payload: dict[str, Any]) -> tuple[DayAllocation, ...]:
    """Flatten a serialized plan payload back into per-day allocations (for /load)."""
    out: list[DayAllocation] = []
    for a in payload.get("assignments", []):
        for al in a.get("allocations", []):
            out.append(
                DayAllocation(
                    person_id=UUID(al["person_id"]),
                    day=date.fromisoformat(al["day"]),
                    hours=al["hours"],
                )
            )
    return tuple(out)


def deserialize_plan(payload: dict[str, Any]) -> PlanResult:
    """Inverse of :func:`serialize_plan` (round-trips a committed plan payload)."""
    assignments = tuple(
        Assignment(
            task_id=UUID(a["task_id"]),
            person_id=UUID(a["person_id"]),
            start_date=date.fromisoformat(a["start_date"]),
            end_date=date.fromisoformat(a["end_date"]),
            allocations=tuple(
                DayAllocation(
                    person_id=UUID(al["person_id"]),
                    day=date.fromisoformat(al["day"]),
                    hours=al["hours"],
                )
                for al in a["allocations"]
            ),
        )
        for a in payload.get("assignments", [])
    )
    risks = tuple(
        RiskFlag(
            kind=r["kind"],
            message=r["message"],
            task_id=_opt_uuid(r.get("task_id")),
            person_id=_opt_uuid(r.get("person_id")),
            day=_opt_date(r.get("day")),
        )
        for r in payload.get("risks", [])
    )
    return PlanResult(
        assignments=assignments, risks=risks, end_date=_opt_date(payload.get("end_date"))
    )


class AddProjectUseCase:
    def __init__(self, repo: RepoPort, solver: SolverPort) -> None:
        self._repo = repo
        self._solver = solver

    async def execute(
        self,
        intent: AddProjectIntent,
        actor: PersonRecord,
        people: tuple[Person, ...],
        template: ProjectTemplate,
        *,
        today: date,
        existing_allocations: tuple[DayAllocation, ...] = (),
        day_overrides: tuple[DayOverride, ...] = (),
    ) -> AddProjectResult:
        title = intent.title.strip()
        if not title:
            raise InvalidProjectError("Название проекта пустое.")
        if intent.deadline is not None and intent.deadline < today:
            raise InvalidProjectError("Дедлайн в прошлом.")
        if not template.tasks:
            raise InvalidProjectError("Шаблон без задач.")

        project_id = uuid4()
        tasks, deps = instantiate_template(template, project_id)
        req = PlanRequest(
            people=people,
            tasks=tasks,
            dependencies=deps,
            horizon_start=today,
            day_overrides=day_overrides,
            existing_allocations=existing_allocations,
            deadline=intent.deadline,
        )

        try:
            plan = self._solver.plan(req)
        except nx.NetworkXUnfeasible as exc:
            raise InvalidProjectError("Цикл в зависимостях шаблона.") from exc
        earliest_end = (
            self._solver.presented_earliest_end(req, today)
            if intent.deadline is None
            else None
        )

        project = await self._repo.create_project(
            title=title,
            template_code=intent.template_code,
            deadline=intent.deadline,
            brief_return_date=intent.brief_return_date,
            actor_id=actor.id,
            project_id=project_id,
        )
        await self._repo.save_project_tasks(project.id, tasks, plan.assignments)

        pv = await self._repo.save_plan_version(
            project.id, "proposed", serialize_plan(plan), actor.id
        )
        await self._repo.add_audit(
            actor.id, "add_project", "project", project.id, {"title": title}
        )

        return AddProjectResult(
            project=project,
            plan_version_id=pv.id,
            plan=plan,
            tasks=tasks,
            earliest_end=earliest_end,
        )
