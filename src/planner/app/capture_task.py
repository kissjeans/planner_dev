"""Capture a chat message straight into the DB as a task (low-friction path).

Resolves a project by name (creating a stub if unknown), falls back to a shared
"Inbox" project, stores the task, and best-effort assigns it. Nothing here ever
asks the user a follow-up question — missing fields simply stay empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from planner.app.ports import (
    PersonRecord,
    ProjectRecord,
    RepoPort,
    SinkTask,
    TaskSinkPort,
)
from planner.domain.intent import CaptureTaskIntent

log = structlog.get_logger(__name__)

INBOX_PROJECT = "Inbox"
_CAPTURE_HOURS = 8


@dataclass(frozen=True)
class CaptureResult:
    task_title: str
    project_title: str
    assignee_names: list[str]
    deadline_iso: str | None
    notion_url: str | None = None
    task_id: UUID | None = None
    duration_hours: int = _CAPTURE_HOURS


class CaptureTaskUseCase:
    def __init__(self, repo: RepoPort, sink: TaskSinkPort | None = None) -> None:
        self._repo = repo
        self._sink = sink

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
        # A hallucinated est_hours <= 0 would create a 0-hour task that corrupts
        # load math; clamp it to the default rather than rejecting the intent.
        est = intent.est_hours
        duration = est if est is not None and est > 0 else _CAPTURE_HOURS
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
        notion_url: str | None = None
        if self._sink is not None:
            try:
                notion_url = await self._sink.push_task(
                    SinkTask(
                        title=intent.task_title,
                        assignees=assignee_names,
                        project=project.title,
                        deadline=intent.deadline,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — Notion mirror is best-effort
                log.warning("notion_mirror_failed", error=str(exc))

        return CaptureResult(
            task_title=intent.task_title,
            project_title=project.title,
            assignee_names=assignee_names,
            deadline_iso=intent.deadline.isoformat() if intent.deadline else None,
            notion_url=notion_url,
            task_id=task.id,
            duration_hours=duration,
        )
