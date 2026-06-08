"""Shared fakes for application-layer use-case tests."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from planner.app.ports import PersonRecord, PlanVersionRecord, ProjectRecord


class FakeRepo:
    """In-memory RepoPort double recording calls for assertions."""

    def __init__(self) -> None:
        self.people: dict[str, PersonRecord] = {}
        self.plan_versions: dict[UUID, PlanVersionRecord] = {}
        self.projects: dict[UUID, ProjectRecord] = {}
        self.overrides: list[tuple[UUID, date, int, str | None]] = []
        self.task_statuses: dict[UUID, str] = {}
        self.audits: list[tuple] = []

    async def set_task_status(self, task_id: UUID, status: str) -> None:
        self.task_statuses[task_id] = status

    async def create_project(
        self, *, title, template_code, deadline, brief_return_date, actor_id
    ) -> ProjectRecord:
        rec = ProjectRecord(uuid4(), title, "planning", deadline)
        self.projects[rec.id] = rec
        return rec

    async def get_solver_people(self):
        return tuple(getattr(self, "solver_people", ()))

    async def get_person_capabilities(self):
        return tuple(getattr(self, "capabilities", ()))

    async def list_committed_plans(self):
        return list(getattr(self, "committed_payloads", []))

    async def get_project_template(self, code: str):
        return getattr(self, "templates", {}).get(code)

    async def get_person_by_name(self, name: str) -> PersonRecord | None:
        return self.people.get(name)

    async def get_plan_version(self, pv_id: UUID) -> PlanVersionRecord | None:
        return self.plan_versions.get(pv_id)

    async def set_plan_version_status(self, pv_id: UUID, status: str) -> None:
        pv = self.plan_versions[pv_id]
        self.plan_versions[pv_id] = PlanVersionRecord(
            pv.id, pv.project_id, status, pv.payload
        )

    async def save_plan_version(self, project_id, status, payload, actor_id):
        rec = PlanVersionRecord(uuid4(), project_id, status, payload)
        self.plan_versions[rec.id] = rec
        return rec

    async def get_committed_plan(self, project_id: UUID) -> PlanVersionRecord | None:
        for pv in self.plan_versions.values():
            if pv.project_id == project_id and pv.status == "committed":
                return pv
        return None

    async def upsert_day_override(self, person_id, day, capacity_h, reason) -> None:
        self.overrides.append((person_id, day, capacity_h, reason))

    async def add_audit(self, actor_id, action, entity_type, entity_id, payload) -> None:
        self.audits.append((actor_id, action, entity_type, entity_id, payload))
