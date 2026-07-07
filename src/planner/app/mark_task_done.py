"""MarkTaskDoneUseCase (spec section 7.4 / 10).

Sets a task's status to ``done`` so the solver pins it on the next replan
(spec invariant 4). Admin-gated and audited, consistent with the other
write use-cases. When a project sink is wired, ``done`` also ticks the
task's checkbox on the project's Notion master card (best-effort mirror,
keyless degrade — never blocks the status write).
"""

from __future__ import annotations

from uuid import UUID

import structlog

from planner.app.ports import ProjectSinkPort, RepoPort

log = structlog.get_logger(__name__)

_ALLOWED_STATUSES = frozenset({"not_done", "done", "preliminary", "confirmed"})


class InvalidStatusError(ValueError):
    pass


class MarkTaskDoneUseCase:
    def __init__(
        self, repo: RepoPort, project_sink: ProjectSinkPort | None = None
    ) -> None:
        self._repo = repo
        self._project_sink = project_sink

    async def execute(
        self, task_id: UUID, actor_id: UUID | None, *, is_admin: bool, status: str = "done"
    ) -> None:
        if not is_admin:
            raise PermissionError("Только админ может менять статус задачи.")
        if status not in _ALLOWED_STATUSES:
            raise InvalidStatusError(status)

        await self._repo.set_task_status(task_id, status)
        await self._repo.add_audit(
            actor_id, "mark_task", "task", task_id, {"status": status}
        )
        if status == "done":
            await self._tick_notion(task_id)

    async def _tick_notion(self, task_id: UUID) -> None:
        """One-way done→checkbox mirror on the project's Notion master card."""
        if self._project_sink is None:
            return
        try:
            metas = await self._repo.list_tasks_with_meta()
            meta = next((t for t in metas if t.task_id == task_id), None)
            if meta is None:
                return
            project = await self._repo.get_project_by_title(meta.project_title)
            if project is None or not project.notion_page_id:
                return
            await self._project_sink.mark_task_done(project.notion_page_id, meta.task_name)
        except Exception as exc:  # noqa: BLE001 — Notion is a best-effort mirror
            log.warning("notion_tick_failed", task_id=str(task_id), error=str(exc))
