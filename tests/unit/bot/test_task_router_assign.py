"""Tests for the AssignIntent reassign orchestrator in the task router (spec 8.1)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from planner.app.ports import PersonRecord, TaskMeta
from planner.bot.handlers.task_router import build_assign_reply
from planner.domain.intent import AssignIntent


class _AssignRepo:
    """Minimal repo double for the assign flow."""

    def __init__(self) -> None:
        self.people: dict[str, PersonRecord] = {}
        self.tasks: list[TaskMeta] = []
        self.set_calls: list[tuple] = []
        self.plan_calls: list[tuple] = []
        self.audits: list[tuple] = []

    async def get_person_by_name(self, name: str) -> PersonRecord | None:
        return self.people.get(name)

    async def list_tasks_with_meta(self) -> list[TaskMeta]:
        return list(self.tasks)

    async def set_task_assignee(self, task_id, person_id, hours: int = 8) -> bool:
        self.set_calls.append((task_id, person_id))
        return any(t.task_id == task_id for t in self.tasks)

    async def reassign_in_plan(self, task_id, new_person_id) -> bool:
        self.plan_calls.append((task_id, new_person_id))
        return True

    async def add_audit(self, *args) -> None:
        self.audits.append(args)


def _task_meta(name: str) -> TaskMeta:
    return TaskMeta(
        task_id=uuid4(),
        task_name=name,
        project_title="Альфа",
        priority="medium",
        status="not_done",
        start_date=None,
        end_date=None,
        duration_hours=8,
        assignee_id=None,
        assignee_name=None,
        deadline=None,
    )


@pytest.mark.asyncio
async def test_assign_unknown_person_asks_to_clarify():
    repo = _AssignRepo()
    repo.tasks = [_task_meta("Дизайн")]
    intent = AssignIntent(task_ref="Дизайн", person_name="Призрак")

    reply = await build_assign_reply(intent, repo=repo, actor_id=uuid4())

    assert "Призрак" in reply
    assert not repo.set_calls


@pytest.mark.asyncio
async def test_assign_unfound_task_asks_to_clarify():
    repo = _AssignRepo()
    repo.people["Андрей"] = PersonRecord(id=uuid4(), name="Андрей")
    repo.tasks = [_task_meta("Дизайн")]
    intent = AssignIntent(task_ref="Несуществующая", person_name="Андрей")

    reply = await build_assign_reply(intent, repo=repo, actor_id=uuid4())

    assert "не наш" in reply.lower() or "уточни" in reply.lower()
    assert not repo.set_calls


@pytest.mark.asyncio
async def test_assign_ambiguous_task_asks_to_clarify():
    repo = _AssignRepo()
    repo.people["Андрей"] = PersonRecord(id=uuid4(), name="Андрей")
    repo.tasks = [_task_meta("Дизайн"), _task_meta("Дизайн")]
    intent = AssignIntent(task_ref="Дизайн", person_name="Андрей")

    reply = await build_assign_reply(intent, repo=repo, actor_id=uuid4())

    assert "уточни" in reply.lower() or "несколько" in reply.lower()
    assert not repo.set_calls


@pytest.mark.asyncio
async def test_assign_resolves_and_reassigns():
    repo = _AssignRepo()
    andrey = PersonRecord(id=uuid4(), name="Андрей")
    repo.people["Андрей"] = andrey
    task = _task_meta("Дизайн")
    repo.tasks = [task]
    intent = AssignIntent(task_ref="дизайн", person_name="Андрей")

    reply = await build_assign_reply(intent, repo=repo, actor_id=uuid4())

    assert "Дизайн" in reply and "Андрей" in reply
    assert repo.set_calls == [(task.task_id, andrey.id)]
    assert repo.plan_calls == [(task.task_id, andrey.id)]


@pytest.mark.asyncio
async def test_assign_substring_match_on_task_ref():
    repo = _AssignRepo()
    andrey = PersonRecord(id=uuid4(), name="Андрей")
    repo.people["Андрей"] = andrey
    task = _task_meta("Дизайн обложки")
    repo.tasks = [task]
    # task_ref like "task: Дизайн in project Альфа" should still match by substring.
    intent = AssignIntent(task_ref="Дизайн обложки в проекте Альфа", person_name="Андрей")

    reply = await build_assign_reply(intent, repo=repo, actor_id=uuid4())

    assert repo.set_calls == [(task.task_id, andrey.id)]
