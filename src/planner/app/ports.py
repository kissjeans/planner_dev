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


@dataclass(frozen=True)
class PersonRecord:
    id: UUID
    name: str
    is_admin: bool = False


@dataclass(frozen=True)
class PlanVersionRecord:
    id: UUID
    project_id: UUID
    status: str  # 'proposed' | 'committed'
    payload: dict[str, Any]


class RepoPort(Protocol):
    async def get_person_by_name(self, name: str) -> PersonRecord | None: ...

    async def get_plan_version(self, pv_id: UUID) -> PlanVersionRecord | None: ...

    async def set_plan_version_status(self, pv_id: UUID, status: str) -> None: ...

    async def save_plan_version(
        self, project_id: UUID, status: str, payload: dict[str, Any], actor_id: UUID | None
    ) -> PlanVersionRecord: ...

    async def get_committed_plan(self, project_id: UUID) -> PlanVersionRecord | None: ...

    async def upsert_day_override(
        self, person_id: UUID, day: date, capacity_h: int, reason: str | None
    ) -> None: ...

    async def add_audit(
        self,
        actor_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        payload: dict[str, Any] | None,
    ) -> None: ...
