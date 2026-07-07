"""SQLAlchemy implementation of RepoPort — the single writer (spec section 17).

Each method opens a short transaction via the session factory. Records returned
are the plain dataclasses from ``app.ports`` so the app/web layers never see ORM
objects (Law of Demeter, spec section 0).
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from planner.app.add_project import ProjectTemplate, TemplateTaskSpec
from planner.app.ports import (
    AuditRecord,
    CapabilityRecord,
    PersonRecord,
    PlanVersionRecord,
    ProjectRecord,
    TaskMeta,
    TaskRecord,
)
from planner.domain.models import (
    Assignment as DomainAssignment,
)
from planner.domain.models import (
    DayOverride as DomainDayOverride,
)
from planner.domain.models import (
    Dependency as DomainDependency,
)
from planner.domain.models import (
    Person as DomainPerson,
)
from planner.domain.models import (
    Task as DomainTask,
)
from planner.infra.db.models import (
    Assignment,
    AuditLog,
    Person,
    PersonRole,
    PlanVersion,
    Project,
    RoleSkill,
    Skill,
    Task,
    Template,
    TemplateDependency,
    TemplateTask,
    TemplateTaskAssignee,
)
from planner.infra.db.models import (
    Dependency as DependencyModel,
)

_CAPTURE_DEFAULT_HOURS = 8


def _person_record(p: Person) -> PersonRecord:
    return PersonRecord(
        id=p.id, name=p.name, is_admin=bool(p.is_admin), capacity_h=p.capacity_h,
        role_label=p.role_label,
    )


def _norm_name(s: str) -> str:
    """Case-fold and treat ё/е as one letter for tolerant name matching."""
    return s.strip().lower().replace("ё", "е")


def match_person_name(candidates: list[str], query: str) -> str | None:
    """Resolve a free-text name to one roster name.

    Exact (case- and ё/е-insensitive) first; else a word in the full name equals
    or starts with the query ("Лёша" -> "Лёша Гарник", "Андр" -> "Андрей …").
    Shortest candidate wins on ties. ``None`` when nothing plausibly matches.
    """
    q = _norm_name(query or "")
    if not q:
        return None
    exact = next((c for c in candidates if _norm_name(c) == q), None)
    if exact is not None:
        return exact
    fuzzy = [
        c for c in candidates
        if any(w == q or w.startswith(q) for w in _norm_name(c).split())
    ]
    return min(fuzzy, key=len) if fuzzy else None


class SqlAlchemyRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_person_by_name(self, name: str) -> PersonRecord | None:
        async with self._sf() as s:
            people = (await s.scalars(select(Person))).all()
        by_name = {p.name: p for p in people}
        chosen = match_person_name(list(by_name), name)
        return _person_record(by_name[chosen]) if chosen is not None else None

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

    async def transition_plan_status(
        self, pv_id: UUID, from_status: str, to_status: str
    ) -> bool:
        """Atomically move a plan version from one status to another.

        Returns True iff exactly this transition happened — a concurrent
        competitor loses because the WHERE clause no longer matches.
        """
        async with self._sf() as s, s.begin():
            result = cast(
                CursorResult[Any],
                await s.execute(
                    update(PlanVersion)
                    .where(PlanVersion.id == pv_id)
                    .where(PlanVersion.status == from_status)
                    .values(status=to_status)
                ),
            )
            return bool(result.rowcount)

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
        priority: str = "medium",
        project_id: UUID | None = None,
    ) -> ProjectRecord:
        project_id = project_id or uuid4()
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
                    priority=priority,
                    status="planning",
                    created_by=actor_id,
                )
            )
        return ProjectRecord(
            project_id, title, "planning", deadline,
            priority=priority, template_code=template_code or None,
        )

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

    async def get_project_by_title(self, title: str) -> ProjectRecord | None:
        async with self._sf() as s:
            p = await s.scalar(
                select(Project).where(func.lower(Project.title) == title.lower())
            )
            if p is None:
                return None
            return ProjectRecord(
                p.id, p.title, p.status, p.deadline,
                notion_page_id=p.notion_page_id,
            )

    async def get_project(self, project_id: UUID) -> ProjectRecord | None:
        async with self._sf() as s:
            p = await s.get(Project, project_id)
            if p is None:
                return None
            return ProjectRecord(
                p.id, p.title, p.status, p.deadline,
                notion_page_id=p.notion_page_id,
            )

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
        task_id = uuid4()
        async with self._sf() as s, s.begin():
            s.add(
                Task(
                    id=task_id,
                    project_id=project_id,
                    name=name,
                    duration_hours=duration_hours,
                    end_date=deadline,
                    status="not_done",
                    source="bot_formed",
                    required_skills=list(required_skills or []),
                )
            )
        return TaskRecord(
            id=task_id,
            name=name,
            status="not_done",
            end_date=deadline,
            duration_hours=duration_hours,
        )

    async def assign_task(self, task_id: UUID, person_id: UUID, hours: int) -> None:
        async with self._sf() as s, s.begin():
            existing = await s.get(
                Assignment, {"task_id": task_id, "person_id": person_id}
            )
            if existing is None:
                s.add(Assignment(task_id=task_id, person_id=person_id, hours=hours))
            else:
                existing.hours = hours

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

    async def list_day_overrides(self) -> tuple[DomainDayOverride, ...]:
        from planner.infra.db.models import DayOverride

        async with self._sf() as s:
            rows = await s.scalars(select(DayOverride))
            return tuple(
                DomainDayOverride(
                    person_id=o.person_id, day=o.day, capacity_h=o.capacity_h
                )
                for o in rows
            )

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

    async def set_project_status(self, project_id: UUID, status: str) -> None:
        async with self._sf() as s, s.begin():
            p = await s.get(Project, project_id)
            if p is not None:
                p.status = status

    async def set_project_notion_page(self, project_id: UUID, page_id: str) -> None:
        async with self._sf() as s, s.begin():
            p = await s.get(Project, project_id)
            if p is not None:
                p.notion_page_id = page_id

    async def add_audit(
        self,
        actor_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        payload: dict[str, Any] | None,
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
            rows = await s.execute(
                select(Project, Template.code)
                .outerjoin(Template, Template.id == Project.template_id)
                .order_by(Project.created_at.desc())
            )
            return [
                ProjectRecord(
                    p.id, p.title, p.status, p.deadline,
                    priority=p.priority,
                    template_code=code,
                    start_date=p.created_at.date() if p.created_at else None,
                )
                for p, code in rows
            ]

    async def list_committed_plans_with_project(
        self,
    ) -> list[tuple[UUID, dict[str, Any]]]:
        async with self._sf() as s:
            rows = await s.scalars(
                select(PlanVersion).where(PlanVersion.status == "committed")
            )
            return [(pv.project_id, pv.payload) for pv in rows]

    async def get_task_name_map(self) -> dict[UUID, str]:
        async with self._sf() as s:
            rows = await s.execute(select(Task.id, Task.name))
            return {tid: name for tid, name in rows}

    async def get_task_project_map(self) -> dict[UUID, str]:
        """Map task id → its project title (for overload/project reporting)."""
        async with self._sf() as s:
            rows = await s.execute(
                select(Task.id, Project.title).join(
                    Project, Task.project_id == Project.id
                )
            )
            return {tid: title for tid, title in rows}

    async def list_tasks_with_meta(self) -> list[TaskMeta]:
        async with self._sf() as s:
            rows = await s.execute(
                select(
                    Task, Project.title, Project.priority, Project.deadline,
                    Person.id, Person.name,
                )
                .join(Project, Project.id == Task.project_id)
                .outerjoin(Assignment, Assignment.task_id == Task.id)
                .outerjoin(Person, Person.id == Assignment.person_id)
                .order_by(Project.title, Task.name)
            )
            out: list[TaskMeta] = []
            for t, title, priority, proj_dl, pid, pname in rows:
                out.append(
                    TaskMeta(
                        task_id=t.id,
                        task_name=t.name,
                        project_title=title,
                        priority=priority,
                        status=t.status,
                        start_date=t.start_date,
                        end_date=t.end_date,
                        duration_hours=t.duration_hours,
                        assignee_id=pid,
                        assignee_name=pname,
                        deadline=proj_dl or t.end_date,
                    )
                )
            return out

    async def set_task_assignee(
        self, task_id: UUID, person_id: UUID, hours: int = 8
    ) -> bool:
        async with self._sf() as s, s.begin():
            task = await s.get(Task, task_id)
            if task is None:
                return False
            existing = await s.scalars(
                select(Assignment).where(Assignment.task_id == task_id)
            )
            for a in existing:
                await s.delete(a)
            s.add(Assignment(task_id=task_id, person_id=person_id, hours=hours))
            return True

    async def reassign_in_plan(self, task_id: UUID, new_person_id: UUID) -> bool:
        task_key = str(task_id)
        person_key = str(new_person_id)
        async with self._sf() as s, s.begin():
            plans = await s.scalars(
                select(PlanVersion).where(PlanVersion.status == "committed")
            )
            for pv in plans:
                payload = dict(pv.payload)
                assignments = [dict(a) for a in payload.get("assignments", [])]
                moved = False
                for a in assignments:
                    if a.get("task_id") == task_key:
                        a["person_id"] = person_key
                        a["allocations"] = [
                            {**dict(al), "person_id": person_key}
                            for al in a.get("allocations", [])
                        ]
                        moved = True
                if moved:
                    payload["assignments"] = assignments
                    pv.payload = payload
                    return True
            return False

    async def update_schedule_in_plan(
        self, task_id: UUID, start: date | None, end: date | None
    ) -> bool:
        task_key = str(task_id)
        async with self._sf() as s, s.begin():
            plans = await s.scalars(
                select(PlanVersion).where(PlanVersion.status == "committed")
            )
            for pv in plans:
                payload = dict(pv.payload)
                assignments = [dict(a) for a in payload.get("assignments", [])]
                moved = False
                for a in assignments:
                    if a.get("task_id") == task_key:
                        if start is not None:
                            a["start_date"] = start.isoformat()
                        if end is not None:
                            a["end_date"] = end.isoformat()
                        moved = True
                if moved:
                    payload["assignments"] = assignments
                    pv.payload = payload
                    return True
            return False

    async def list_project_tasks(self, project_id: UUID) -> list[TaskRecord]:
        async with self._sf() as s:
            rows = await s.scalars(
                select(Task).where(Task.project_id == project_id).order_by(Task.name)
            )
            return [
                TaskRecord(
                    id=t.id,
                    name=t.name,
                    status=t.status,
                    start_date=t.start_date,
                    end_date=t.end_date,
                    duration_hours=t.duration_hours,
                )
                for t in rows
            ]

    async def list_people(self) -> list[PersonRecord]:
        async with self._sf() as s:
            rows = await s.scalars(select(Person).order_by(Person.name))
            return [_person_record(p) for p in rows]

    async def list_committed_plans(self) -> list[dict[str, Any]]:
        async with self._sf() as s:
            rows = await s.scalars(
                select(PlanVersion).where(PlanVersion.status == "committed")
            )
            return [pv.payload for pv in rows]

    async def reset_all_plans(self) -> list[str]:
        """Supersede every committed plan and cancel its project (zeroes load).

        Load is the sum of committed-plan allocations, so superseding them all
        drops every person's load to zero. Returns the Notion master-card page
        ids of the affected projects so the caller can archive them and keep the
        DB↔Notion mirror consistent.
        """
        async with self._sf() as s, s.begin():
            committed = list(
                await s.scalars(
                    select(PlanVersion).where(PlanVersion.status == "committed")
                )
            )
            project_ids = {pv.project_id for pv in committed}
            for pv in committed:
                pv.status = "superseded"
            pages: list[str] = []
            for pid in project_ids:
                proj = await s.get(Project, pid)
                if proj is None:
                    continue
                proj.status = "cancelled"
                if proj.notion_page_id:
                    pages.append(proj.notion_page_id)
        return pages

    async def list_task_dependencies(self) -> list[DomainDependency]:
        async with self._sf() as s:
            rows = await s.scalars(select(DependencyModel))
            return [
                DomainDependency(
                    task_id=r.task_id,
                    depends_on_id=r.depends_on_id,
                    link_type=r.link_type,
                )
                for r in rows
            ]

    async def get_person_capabilities(self) -> tuple[CapabilityRecord, ...]:
        async with self._sf() as s:
            rows = await s.execute(
                select(Person.id, Person.name, Person.is_external, Skill.name)
                .select_from(Person)
                .outerjoin(PersonRole, PersonRole.person_id == Person.id)
                .outerjoin(RoleSkill, RoleSkill.role_id == PersonRole.role_id)
                .outerjoin(Skill, Skill.id == RoleSkill.skill_id)
                .where(Person.is_active.is_(True))
                .order_by(Person.name)
            )
            agg: dict[UUID, dict[str, Any]] = {}
            for pid, name, is_external, skill_name in rows:
                entry = agg.setdefault(
                    pid, {"name": name, "is_external": bool(is_external), "skills": set()}
                )
                if skill_name is not None:
                    entry["skills"].add(skill_name)
            return tuple(
                CapabilityRecord(
                    person_id=pid,
                    name=e["name"],
                    skills=frozenset(e["skills"]),
                    is_external=e["is_external"],
                )
                for pid, e in agg.items()
            )

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

            # Order assignees by priority (0 = highest) so the solver's
            # priority-aware executor pick sees the binding in the right order.
            assignees: dict[UUID, list[UUID]] = {}
            for row in await s.scalars(
                select(TemplateTaskAssignee)
                .where(TemplateTaskAssignee.template_task_id.in_(id_to_ord))
                .order_by(TemplateTaskAssignee.priority, TemplateTaskAssignee.strictness)
            ):
                assignees.setdefault(row.template_task_id, []).append(row.person_id)

            deps: dict[UUID, list[tuple[int, str, int]]] = {}
            for dep_row in await s.scalars(
                select(TemplateDependency).where(
                    TemplateDependency.template_task_id.in_(id_to_ord)
                )
            ):
                dep_ord = id_to_ord.get(dep_row.depends_on_id)
                if dep_ord is not None:
                    deps.setdefault(dep_row.template_task_id, []).append(
                        (dep_ord, dep_row.link_type, dep_row.lag_working_days)
                    )

            specs = tuple(
                TemplateTaskSpec(
                    ord=tt.ord,
                    name=tt.name,
                    duration_hours=tt.duration_hours,
                    allowed_person_ids=tuple(assignees.get(tt.id, ())),
                    depends_on_ords=tuple(o for o, _, _ in deps.get(tt.id, ())),
                    link_types=tuple(lt for _, lt, _ in deps.get(tt.id, ())),
                    dep_lags=tuple(lag for _, _, lag in deps.get(tt.id, ())),
                    is_splittable=bool(tt.is_splittable),
                    pair_mode=tt.pair_mode,
                    duration_is_window=bool(tt.duration_is_window),
                )
                for tt in tt_rows
            )
        return ProjectTemplate(code=code, tasks=specs)

    async def save_project_tasks(
        self,
        project_id: UUID,
        tasks: tuple[DomainTask, ...],
        assignments: tuple[DomainAssignment, ...],
    ) -> None:
        """Persist instantiated template tasks with their planned schedule.

        One transaction: either the whole task set lands or none of it.
        """
        by_task = {a.task_id: a for a in assignments}
        async with self._sf() as s, s.begin():
            for t in tasks:
                a = by_task.get(t.id)
                s.add(
                    Task(
                        id=t.id,
                        project_id=project_id,
                        name=t.name,
                        duration_hours=t.duration_hours,
                        start_date=a.start_date if a else None,
                        end_date=a.end_date if a else None,
                        status="not_done",
                        source=t.source,
                        required_skills=list(t.required_skills),
                        is_splittable=t.is_splittable,
                        pair_mode=t.pair_mode,
                        duration_is_window=t.duration_is_window,
                    )
                )
            # Tasks must hit the DB before their assignments: assignments.task_id
            # has a FK -> tasks.id, and the interleaved add() order is not a
            # guaranteed INSERT order. Flush the tasks first.
            await s.flush()
            for t in tasks:
                a = by_task.get(t.id)
                if a is None:
                    continue
                # One row per distinct person; a required pair yields two rows.
                # A window task has no allocations — record its single assignee.
                hours_by_person: dict[UUID, int] = {}
                for al in a.allocations:
                    hours_by_person[al.person_id] = (
                        hours_by_person.get(al.person_id, 0) + al.hours
                    )
                if not hours_by_person:
                    hours_by_person[a.person_id] = t.duration_hours
                for person_id, hours in hours_by_person.items():
                    s.add(Assignment(task_id=t.id, person_id=person_id, hours=hours))

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
                    entity_id=a.entity_id,
                )
                for a in rows
            ]
