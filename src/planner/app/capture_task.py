"""Capture a chat message straight into the DB as a task (low-friction path).

Resolves a project by name (creating a stub if unknown), falls back to a shared
"Inbox" project, stores the task, and best-effort assigns it. Nothing here ever
asks the user a follow-up question — missing fields simply stay empty.
"""

from __future__ import annotations

from dataclasses import dataclass

from planner.app.ports import PersonRecord, ProjectRecord, RepoPort
from planner.domain.intent import CaptureTaskIntent

INBOX_PROJECT = "Inbox"
_CAPTURE_HOURS = 8


@dataclass(frozen=True)
class CaptureResult:
    task_title: str
    project_title: str
    assignee_names: list[str]
    deadline_iso: str | None


class CaptureTaskUseCase:
    def __init__(self, repo: RepoPort) -> None:
        self._repo = repo

    async def _resolve_project(
        self, name: str | None, actor: PersonRecord | None
    ) -> ProjectRecord:
        actor_uuid = actor.id if actor else None
        target = (name or "").strip() or INBOX_PROJECT
        existing = await self._repo.get_project_by_title(target)
        if existing is not None:
            return existing
        return await self._repo.create_project(
            title=target,
            template_code="",  # capture stub: no template
            deadline=None,
            brief_return_date=None,
            actor_id=actor_uuid,
        )

    async def execute(
        self, intent: CaptureTaskIntent, actor: PersonRecord | None
    ) -> CaptureResult:
        project = await self._resolve_project(intent.project_name, actor)
        duration = intent.est_hours if intent.est_hours is not None else _CAPTURE_HOURS
        task = await self._repo.create_task(
            project_id=project.id,
            name=intent.task_title,
            duration_hours=duration,
            deadline=intent.deadline,
            actor_id=actor.id if actor else None,
            required_skills=list(intent.required_skills),
        )

        assignee_names: list[str] = []
        for name in intent.assignee_names:
            person = await self._repo.get_person_by_name(name)
            if person is not None:
                await self._repo.assign_task(task.id, person.id, duration)
                assignee_names.append(person.name)

        await self._repo.add_audit(
            actor.id if actor else None,
            "capture_task",
            "task",
            task.id,
            {
                "title": intent.task_title,
                "project": project.title,
                "assignees": assignee_names,
            },
        )
        return CaptureResult(
            task_title=intent.task_title,
            project_title=project.title,
            assignee_names=assignee_names,
            deadline_iso=intent.deadline.isoformat() if intent.deadline else None,
        )
