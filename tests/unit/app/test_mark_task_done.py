"""Unit tests for MarkTaskDoneUseCase (spec section 7.4)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from planner.app.mark_task_done import InvalidStatusError, MarkTaskDoneUseCase
from planner.app.ports import ProjectRecord, TaskMeta
from tests.unit.app.conftest import FakeRepo

_ACTOR_ID = uuid4()


def _meta(task_id: UUID, name: str, project_title: str) -> TaskMeta:
    return TaskMeta(
        task_id=task_id, task_name=name, project_title=project_title,
        priority="medium", status="not_done", start_date=None, end_date=None,
        duration_hours=8, assignee_id=None, assignee_name=None, deadline=None,
    )


class _MetaRepo(FakeRepo):
    """FakeRepo + the task→project lookups the Notion tick needs."""

    def __init__(self, metas: list[TaskMeta], project: ProjectRecord) -> None:
        super().__init__()
        self.metas = metas
        self.project = project

    async def list_tasks_with_meta(self) -> list[TaskMeta]:
        return self.metas

    async def get_project_by_title(self, title: str) -> ProjectRecord | None:
        if self.project.title.casefold() == title.casefold():
            return self.project
        return None


class _RecordingSink:
    def __init__(self) -> None:
        self.ticked: list[tuple[str, str]] = []

    async def mark_task_done(self, page_id: str, task_name: str) -> bool:
        self.ticked.append((page_id, task_name))
        return True


@pytest.mark.asyncio
async def test_marks_task_done_and_audits():
    repo = FakeRepo()
    task_id = uuid4()
    await MarkTaskDoneUseCase(repo).execute(task_id, _ACTOR_ID, is_admin=True)

    assert repo.task_statuses[task_id] == "done"
    assert any(a[1] == "mark_task" for a in repo.audits)


@pytest.mark.asyncio
async def test_custom_status_allowed():
    repo = FakeRepo()
    task_id = uuid4()
    await MarkTaskDoneUseCase(repo).execute(
        task_id, _ACTOR_ID, is_admin=True, status="confirmed"
    )
    assert repo.task_statuses[task_id] == "confirmed"


@pytest.mark.asyncio
async def test_invalid_status_rejected():
    repo = FakeRepo()
    with pytest.raises(InvalidStatusError):
        await MarkTaskDoneUseCase(repo).execute(
            uuid4(), _ACTOR_ID, is_admin=True, status="bogus"
        )


@pytest.mark.asyncio
async def test_non_admin_blocked():
    repo = FakeRepo()
    with pytest.raises(PermissionError):
        await MarkTaskDoneUseCase(repo).execute(uuid4(), _ACTOR_ID, is_admin=False)
    assert repo.task_statuses == {}


@pytest.mark.asyncio
async def test_done_ticks_notion_checklist():
    task_id = uuid4()
    project = ProjectRecord(uuid4(), "Альфа", "active", notion_page_id="page-1")
    repo = _MetaRepo([_meta(task_id, "дизайн", "Альфа")], project)
    sink = _RecordingSink()

    await MarkTaskDoneUseCase(repo, project_sink=sink).execute(
        task_id, _ACTOR_ID, is_admin=True
    )

    assert repo.task_statuses[task_id] == "done"
    assert sink.ticked == [("page-1", "дизайн")]


@pytest.mark.asyncio
async def test_non_done_status_does_not_tick_notion():
    task_id = uuid4()
    project = ProjectRecord(uuid4(), "Альфа", "active", notion_page_id="page-1")
    repo = _MetaRepo([_meta(task_id, "дизайн", "Альфа")], project)
    sink = _RecordingSink()

    await MarkTaskDoneUseCase(repo, project_sink=sink).execute(
        task_id, _ACTOR_ID, is_admin=True, status="confirmed"
    )

    assert sink.ticked == []


@pytest.mark.asyncio
async def test_project_without_notion_page_skips_tick():
    task_id = uuid4()
    project = ProjectRecord(uuid4(), "Альфа", "active")  # never mirrored to Notion
    repo = _MetaRepo([_meta(task_id, "дизайн", "Альфа")], project)
    sink = _RecordingSink()

    await MarkTaskDoneUseCase(repo, project_sink=sink).execute(
        task_id, _ACTOR_ID, is_admin=True
    )

    assert repo.task_statuses[task_id] == "done"
    assert sink.ticked == []


@pytest.mark.asyncio
async def test_sink_failure_never_blocks_done():
    class _BoomSink:
        async def mark_task_done(self, page_id: str, task_name: str) -> bool:
            raise RuntimeError("notion down")

    task_id = uuid4()
    project = ProjectRecord(uuid4(), "Альфа", "active", notion_page_id="page-1")
    repo = _MetaRepo([_meta(task_id, "дизайн", "Альфа")], project)

    await MarkTaskDoneUseCase(repo, project_sink=_BoomSink()).execute(
        task_id, _ACTOR_ID, is_admin=True
    )

    assert repo.task_statuses[task_id] == "done"
