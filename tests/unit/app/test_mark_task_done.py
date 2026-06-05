"""Unit tests for MarkTaskDoneUseCase (spec section 7.4)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from planner.app.mark_task_done import InvalidStatusError, MarkTaskDoneUseCase
from planner.app.ports import PersonRecord
from tests.unit.app.conftest import FakeRepo


def _admin() -> PersonRecord:
    return PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)


def _member() -> PersonRecord:
    return PersonRecord(id=uuid4(), name="Рай", is_admin=False)


@pytest.mark.asyncio
async def test_marks_task_done_and_audits():
    repo = FakeRepo()
    task_id = uuid4()
    await MarkTaskDoneUseCase(repo).execute(task_id, _admin())

    assert repo.task_statuses[task_id] == "done"
    assert any(a[1] == "mark_task" for a in repo.audits)


@pytest.mark.asyncio
async def test_custom_status_allowed():
    repo = FakeRepo()
    task_id = uuid4()
    await MarkTaskDoneUseCase(repo).execute(task_id, _admin(), status="confirmed")
    assert repo.task_statuses[task_id] == "confirmed"


@pytest.mark.asyncio
async def test_invalid_status_rejected():
    repo = FakeRepo()
    with pytest.raises(InvalidStatusError):
        await MarkTaskDoneUseCase(repo).execute(uuid4(), _admin(), status="bogus")


@pytest.mark.asyncio
async def test_non_admin_blocked():
    repo = FakeRepo()
    with pytest.raises(PermissionError):
        await MarkTaskDoneUseCase(repo).execute(uuid4(), _member())
    assert repo.task_statuses == {}
