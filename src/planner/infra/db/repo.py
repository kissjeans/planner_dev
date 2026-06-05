"""SQLAlchemy implementation of RepoPort — the single writer (spec section 17).

Each method opens a short transaction via the session factory. Records returned
are the plain dataclasses from ``app.ports`` so the app/web layers never see ORM
objects (Law of Demeter, spec section 0).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from planner.app.add_project import ProjectTemplate, TemplateTaskSpec
from planner.app.ports import (
    AuditRecord,
    PersonRecord,
    PlanVersionRecord,
    ProjectRecord,
)
from planner.domain.models import Person as DomainPerson
from planner.infra.db.models import (
    AuditLog,
    Person,
    PlanVersion,
    Project,
    Task,
    Template,
    TemplateDependency,
    TemplateTask,
    TemplateTaskAssignee,
)


def _person_record(p: Person) -> PersonRecord:
    return PersonRecord(id=p.id, name=p.name, is_admin=bool(p.is_admin))


class SqlAlchemyRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_person_by_name(self, name: str) -> PersonRecord | None:
        async with self._sf() as s:
            p = await s.scalar(select(Person).where(Person.name == name))
            return _person_record(p) if p else None

    async def get_person_by_tg_id(self, tg_user_id: int) -> PersonRecord | None:
        async with self._sf() as s:
            p = await s.scalar(select(Person).where(Person.tg_user_id == tg_user_id))
            return _person_record(p) if p else None

    async def get_plan_version(self, pv_id: UUID) -> PlanVersionRecord | None:
        async with self._sf() as s:
            pv = await s.get(PlanVersion, pv_id)
            if pv is None:
                return None
            return PlanVersionRecord(pv.id, pv.project_id, pv.status, pv.payload)

    async def set_plan_version_status(self, pv_id: UUID, status: str) -> None:
        async with self._sf() as s, s.begin():
            pv = await s.get(PlanVersion, pv_id)
            if pv is not None:
                pv.status = status

    async def save_plan_version(
        self, project_id: UUID, status: str, payload: dict[str, Any], actor_id: UUID | None
    ) -> PlanVersionRecord:
        pv_id = uuid4()
        async with self._sf() as s, s.begin():
            s.add(
                PlanVersion(
                    id=pv_id,
                    project_id=project_id,
                    status=status,
                    payload=payload,
                    created_by=actor_id,
                )
            )
        return PlanVersionRecord(pv_id, project_id, status, payload)

    async def create_project(
        self,
        *,
        title: str,
        template_code: str,
        deadline: date | None,
        brief_return_date: date | None,
        actor_id: UUID | None,
    ) -> ProjectRecord:
        project_id = uuid4()
        async with self._sf() as s, s.begin():
            template_id = await s.scalar(
                select(Template.id).where(Template.code == template_code)
            )
            s.add(
                Project(
                    id=project_id,
                    title=title,
                    template_id=template_id,
                    deadline=deadline,
                    brief_return_date=brief_return_date,
                    status="planning",
                    created_by=actor_id,
                )
            )
        return ProjectRecord(project_id, title, "planning", deadline)

    async def get_committed_plan(self, project_id: UUID) -> PlanVersionRecord | None:
        async with self._sf() as s:
            pv = await s.scalar(
                select(PlanVersion)
                .where(PlanVersion.project_id == project_id)
                .where(PlanVersion.status == "committed")
                .order_by(PlanVersion.created_at.desc())
            )
            if pv is None:
                return None
            return PlanVersionRecord(pv.id, pv.project_id, pv.status, pv.payload)

    async def upsert_day_override(
        self, person_id: UUID, day: date, capacity_h: int, reason: str | None
    ) -> None:
        from planner.infra.db.models import DayOverride

        async with self._sf() as s, s.begin():
            existing = await s.get(DayOverride, {"person_id": person_id, "day": day})
            if existing is None:
                s.add(
                    DayOverride(
                        person_id=person_id,
                        day=day,
                        capacity_h=capacity_h,
                        reason=reason,
                    )
                )
            else:
                existing.capacity_h = capacity_h
                existing.reason = reason

    async def update_task_schedule(
        self, task_id: UUID, start: date | None, end: date | None, person_id: UUID | None
    ) -> None:
        async with self._sf() as s, s.begin():
            t = await s.get(Task, task_id)
            if t is None:
                return
            if start is not None:
                t.start_date = start
            if end is not None:
                t.end_date = end

    async def set_task_status(self, task_id: UUID, status: str) -> None:
        async with self._sf() as s, s.begin():
            t = await s.get(Task, task_id)
            if t is not None:
                t.status = status

    async def add_audit(
        self, actor_id, action, entity_type, entity_id, payload
    ) -> None:
        async with self._sf() as s, s.begin():
            s.add(
                AuditLog(
                    actor_id=actor_id,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    payload=payload,
                )
            )

    async def list_projects(self) -> list[ProjectRecord]:
        async with self._sf() as s:
            rows = await s.scalars(select(Project).order_by(Project.created_at.desc()))
            return [
                ProjectRecord(p.id, p.title, p.status, p.deadline) for p in rows
            ]

    async def list_people(self) -> list[PersonRecord]:
        async with self._sf() as s:
            rows = await s.scalars(select(Person).order_by(Person.name))
            return [_person_record(p) for p in rows]

    async def get_solver_people(self) -> tuple[DomainPerson, ...]:
        async with self._sf() as s:
            rows = await s.scalars(
                select(Person).where(Person.is_active.is_(True)).order_by(Person.name)
            )
            return tuple(
                DomainPerson(id=p.id, name=p.name, capacity_h=p.capacity_h) for p in rows
            )

    async def get_project_template(self, code: str) -> ProjectTemplate | None:
        async with self._sf() as s:
            template = await s.scalar(select(Template).where(Template.code == code))
            if template is None:
                return None

            tt_rows = list(
                await s.scalars(
                    select(TemplateTask)
                    .where(TemplateTask.template_id == template.id)
                    .order_by(TemplateTask.ord)
                )
            )
            id_to_ord = {tt.id: tt.ord for tt in tt_rows}

            assignees: dict[UUID, list[UUID]] = {}
            for row in await s.scalars(
                select(TemplateTaskAssignee).where(
                    TemplateTaskAssignee.template_task_id.in_(id_to_ord)
                )
            ):
                assignees.setdefault(row.template_task_id, []).append(row.person_id)

            deps: dict[UUID, list[tuple[int, str]]] = {}
            for row in await s.scalars(
                select(TemplateDependency).where(
                    TemplateDependency.template_task_id.in_(id_to_ord)
                )
            ):
                dep_ord = id_to_ord.get(row.depends_on_id)
                if dep_ord is not None:
                    deps.setdefault(row.template_task_id, []).append(
                        (dep_ord, row.link_type)
                    )

            specs = tuple(
                TemplateTaskSpec(
                    ord=tt.ord,
                    name=tt.name,
                    duration_hours=tt.duration_hours,
                    allowed_person_ids=tuple(assignees.get(tt.id, ())),
                    depends_on_ords=tuple(o for o, _ in deps.get(tt.id, ())),
                    link_types=tuple(lt for _, lt in deps.get(tt.id, ())),
                    is_splittable=bool(tt.is_splittable),
                    allow_two_assignees=bool(tt.allow_two_assignees),
                )
                for tt in tt_rows
            )
        return ProjectTemplate(code=code, tasks=specs)

    async def list_audit(self, limit: int = 50, offset: int = 0) -> list[AuditRecord]:
        async with self._sf() as s:
            rows = await s.scalars(
                select(AuditLog)
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [
                AuditRecord(
                    created_at=str(a.created_at),
                    action=a.action,
                    entity_type=a.entity_type,
                    payload=a.payload,
                )
                for a in rows
            ]
