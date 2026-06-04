"""Shared fakes for application-layer use-case tests."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from planner.app.ports import PersonRecord, PlanVersionRecord


class FakeRepo:
    """In-memory RepoPort double recording calls for assertions."""

    def __init__(self) -> None:
        self.people: dict[str, PersonRecord] = {}
        self.plan_versions: dict[UUID, PlanVersionRecord] = {}
        self.overrides: list[tuple[UUID, date, int, str | None]] = []
        self.audits: list[tuple] = []

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
