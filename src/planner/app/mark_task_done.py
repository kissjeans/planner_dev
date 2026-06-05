"""MarkTaskDoneUseCase (spec section 7.4 / 10).

Sets a task's status to ``done`` so the solver pins it on the next replan
(spec invariant 4). Admin-gated and audited, consistent with the other
write use-cases.
"""

from __future__ import annotations

from uuid import UUID

from planner.app.ports import PersonRecord, RepoPort

_ALLOWED_STATUSES = frozenset({"not_done", "done", "preliminary", "confirmed"})


class InvalidStatusError(ValueError):
    pass


class MarkTaskDoneUseCase:
    def __init__(self, repo: RepoPort) -> None:
        self._repo = repo

    async def execute(
        self, task_id: UUID, actor: PersonRecord, *, status: str = "done"
    ) -> None:
        if not actor.is_admin:
            raise PermissionError("Только админ может менять статус задачи.")
        if status not in _ALLOWED_STATUSES:
            raise InvalidStatusError(status)

        await self._repo.set_task_status(task_id, status)
        await self._repo.add_audit(
            actor.id, "mark_task", "task", task_id, {"status": status}
        )
