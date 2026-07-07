"""ConfirmPlanUseCase (spec section 7.3).

Promotes a proposed plan version to committed — any chat participant may
confirm (membership gates upstream in the handler) — and records an audit
entry. The DB transaction boundary is the repo's concern.

On commit it MIRRORS the plan's tasks into Notion (one board row per task with
its assignee, scheduled date and project) so the Notion mirror reflects the DB —
the user's source of truth is Telegram + Notion, never Postgres directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from planner.app.ports import (
    PersonRecord,
    PlanVersionRecord,
    RepoPort,
    SinkTask,
    TaskSinkPort,
)

log = structlog.get_logger(__name__)


class PlanNotFoundError(Exception):
    pass


class PlanNotProposedError(Exception):
    pass


@dataclass(frozen=True)
class ConfirmResult:
    """Outcome of a confirm: the committed plan plus Notion mirror stats."""

    plan: PlanVersionRecord
    mirror_total: int = 0
    mirror_failed: int = 0


class ConfirmPlanUseCase:
    def __init__(self, repo: RepoPort, task_sink: TaskSinkPort | None = None) -> None:
        self._repo = repo
        self._sink = task_sink

    async def execute(
        self, plan_version_id: UUID, actor: PersonRecord | None
    ) -> ConfirmResult:
        # Any chat participant can confirm (membership already gates upstream in
        # the handler) — registration and admin rights are not required.
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
            actor.id if actor is not None else None,
            "confirm_plan", "plan_version", plan_version_id, None,
        )
        # Reflect the commit on the project so the board stops showing 'planning'.
        await self._repo.set_project_status(pv.project_id, "committed")
        # Mirror the committed tasks into Notion (best-effort, never blocks).
        failed, total = await self._mirror_tasks(pv)
        return ConfirmResult(
            plan=PlanVersionRecord(
                id=pv.id, project_id=pv.project_id, status="committed", payload=pv.payload
            ),
            mirror_total=total,
            mirror_failed=failed,
        )

    async def _mirror_tasks(self, pv: PlanVersionRecord) -> tuple[int, int]:
        """Push one Notion board row per committed task (name/assignee/date/project).

        Returns ``(failed, total)`` so the caller can warn on a partial mirror.
        """
        if self._sink is None or getattr(self._sink, "is_noop", False):
            return 0, 0  # keyless degrade: nothing mirrored, nothing to warn about
        succeeded = total = 0
        try:
            from planner.app.add_project import deserialize_plan

            project = await self._repo.get_project(pv.project_id)
            title = project.title if project else "Проект"
            names = await self._repo.get_task_name_map()
            people = {p.id: p.name for p in await self._repo.list_people()}
            plan = deserialize_plan(pv.payload)
            total = len(plan.assignments)
            for a in plan.assignments:
                url = await self._sink.push_task(
                    SinkTask(
                        title=names.get(a.task_id, "Задача"),
                        assignees=[people[a.person_id]] if a.person_id in people else [],
                        project=title,
                        deadline=a.end_date,
                    )
                )
                if url is not None:  # push_task swallows errors and signals with None
                    succeeded += 1
        except Exception:  # noqa: BLE001 — Notion is a best-effort mirror
            log.warning("plan_mirror_failed", project_id=str(pv.project_id))
        # Mid-loop crash: never-attempted tasks count as failed — they are not on the board.
        return total - succeeded, total
