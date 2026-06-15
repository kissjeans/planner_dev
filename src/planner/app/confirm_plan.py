"""ConfirmPlanUseCase (spec section 7.3).

Promotes a proposed plan version to committed, in admin-gated fashion, and
records an audit entry. The DB transaction boundary is the repo's concern.
"""

from __future__ import annotations

from uuid import UUID

from planner.app.ports import PersonRecord, PlanVersionRecord, RepoPort


class PlanNotFoundError(Exception):
    pass


class PlanNotProposedError(Exception):
    pass


class ConfirmPlanUseCase:
    def __init__(self, repo: RepoPort) -> None:
        self._repo = repo

    async def execute(
        self, plan_version_id: UUID, actor: PersonRecord
    ) -> PlanVersionRecord:
        if not actor.is_admin:
            raise PermissionError("Только админ может подтверждать план.")

        pv = await self._repo.get_plan_version(plan_version_id)
        if pv is None:
            raise PlanNotFoundError(str(plan_version_id))

        moved = await self._repo.transition_plan_status(
            plan_version_id, "proposed", "committed"
        )
        if not moved:
            # Lost the race or never proposed — re-read for the precise status.
            current = await self._repo.get_plan_version(plan_version_id)
            raise PlanNotProposedError(current.status if current else "missing")

        await self._repo.add_audit(
            actor.id, "confirm_plan", "plan_version", plan_version_id, None
        )
        return PlanVersionRecord(
            id=pv.id, project_id=pv.project_id, status="committed", payload=pv.payload
        )
