"""Tests for the capture reply orchestrator in the task router (spec 3/5).

The captured task draft is enriched with LLM-inferred fields. When no assignee
is named but the task carries required skills, the reply appends a *suggestion*
(«Предлагаю: …») — it never auto-assigns (spec section 5).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from planner.app.ports import CapabilityRecord, PersonRecord, ProjectRecord, TaskRecord
from planner.bot.handlers.task_router import build_capture_reply
from planner.domain.intent import CaptureTaskIntent


class _CaptureRepo:
    """Minimal repo double for the capture + suggest flow."""

    def __init__(self, *, capabilities=(), people=None) -> None:
        self.capabilities = tuple(capabilities)
        self._people = people or {}
        self.assignments: list[tuple] = []
        self.created_tasks: list[dict[str, Any]] = []
        self.audits: list[tuple] = []

    async def get_project_by_title(self, title: str) -> ProjectRecord | None:
        return None

    async def create_project(self, *, title, template_code, deadline,
                             brief_return_date, actor_id) -> ProjectRecord:
        return ProjectRecord(uuid4(), title, "planning", deadline)

    async def create_task(self, *, project_id, name, duration_hours, deadline,
                          actor_id, required_skills=None) -> TaskRecord:
        self.created_tasks.append({"name": name, "required_skills": required_skills})
        return TaskRecord(id=uuid4(), name=name, status="not_done",
                          end_date=deadline, duration_hours=duration_hours)

    async def get_person_by_name(self, name: str) -> PersonRecord | None:
        return self._people.get(name)

    async def assign_task(self, task_id, person_id, hours) -> None:
        self.assignments.append((task_id, person_id, hours))

    async def add_audit(self, *args) -> None:
        self.audits.append(args)

    async def get_person_capabilities(self) -> tuple[CapabilityRecord, ...]:
        return self.capabilities

    async def list_committed_plans(self) -> list[dict[str, Any]]:
        return []


_ADMIN = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)


@pytest.mark.asyncio
async def test_suggests_assignee_when_none_named_and_skills_present():
    designer = CapabilityRecord(
        person_id=uuid4(), name="Дима", skills=frozenset({"дизайн"})
    )
    other = CapabilityRecord(
        person_id=uuid4(), name="Олег", skills=frozenset({"аналитика"})
    )
    repo = _CaptureRepo(capabilities=(designer, other))

    intent = CaptureTaskIntent(
        task_title="нарисовать макет", required_skills=["дизайн"]
    )
    reply, _ = await build_capture_reply(intent, repo=repo, actor_record=_ADMIN)

    assert "✓ Записал" in reply
    assert "предлагаю" in reply.lower()
    assert "Дима" in reply
    # Suggestion only — never auto-assigns (spec 5).
    assert repo.assignments == []


@pytest.mark.asyncio
async def test_no_suggestion_when_assignee_named():
    andrey = PersonRecord(id=uuid4(), name="Андрей")
    designer = CapabilityRecord(
        person_id=uuid4(), name="Дима", skills=frozenset({"дизайн"})
    )
    repo = _CaptureRepo(capabilities=(designer,), people={"Андрей": andrey})

    intent = CaptureTaskIntent(
        task_title="нарисовать макет", assignee_names=["Андрей"],
        required_skills=["дизайн"],
    )
    reply, _ = await build_capture_reply(intent, repo=repo, actor_record=_ADMIN)

    assert "предлагаю" not in reply.lower()
    assert repo.assignments and repo.assignments[0][1] == andrey.id


@pytest.mark.asyncio
async def test_no_suggestion_when_no_skills():
    designer = CapabilityRecord(
        person_id=uuid4(), name="Дима", skills=frozenset({"дизайн"})
    )
    repo = _CaptureRepo(capabilities=(designer,))

    intent = CaptureTaskIntent(task_title="позвонить клиенту")
    reply, _ = await build_capture_reply(intent, repo=repo, actor_record=_ADMIN)

    assert "предлагаю" not in reply.lower()


@pytest.mark.asyncio
async def test_no_suggestion_when_no_matching_candidate():
    other = CapabilityRecord(
        person_id=uuid4(), name="Олег", skills=frozenset({"аналитика"})
    )
    repo = _CaptureRepo(capabilities=(other,))

    intent = CaptureTaskIntent(
        task_title="нарисовать макет", required_skills=["дизайн"]
    )
    reply, _ = await build_capture_reply(intent, repo=repo, actor_record=_ADMIN)

    assert "предлагаю" not in reply.lower()
