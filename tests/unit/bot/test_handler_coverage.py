"""Coverage tests for load, whatif, and task_router handlers (uncovered paths)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from planner.bot.handlers import load as load_handler
from planner.bot.handlers import whatif as whatif_handler
from planner.bot.handlers.task_router import _handle_text, describe_intent
from planner.domain.calendar.rules import WeekendCalendar
from planner.domain.intent import (
    AddProjectIntent,
    AssignIntent,
    ClarifyIntent,
    ConfirmIntent,
    LoadIntent,
    VacationIntent,
    WhatIfIntent,
)
from planner.domain.models import Person
from planner.domain.solver.greedy import GreedySolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Answers:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.photos: list[Any] = []

    async def answer(self, text: str = "", **kwargs: Any) -> None:
        self.calls.append(text)

    async def answer_photo(self, photo: Any, caption: str = "", **kwargs: Any) -> None:
        self.photos.append((photo, caption))


def _message(text: str = "", chat_type: str = "private") -> tuple[SimpleNamespace, _Answers]:
    answers = _Answers()
    chat = SimpleNamespace(type=chat_type)
    msg = SimpleNamespace(
        text=text,
        answer=answers.answer,
        answer_photo=answers.answer_photo,
        chat=chat,
        reply_to_message=None,
        bot=None,
    )
    return msg, answers


class _FakeParser:
    def __init__(self, intent: Any) -> None:
        self._intent = intent

    async def parse(self, text: str, ctx: Any) -> Any:
        return self._intent


class _FakeRepo:
    def __init__(self, people=(), plans=()) -> None:
        self._people = people
        self._plans = list(plans)

    async def get_solver_people(self) -> tuple:
        return self._people

    async def list_committed_plans(self) -> list:
        return self._plans

    async def get_project_template(self, code: str) -> None:
        return None


# ---------------------------------------------------------------------------
# describe_intent
# ---------------------------------------------------------------------------

def test_describe_intent_add_project():
    intent = AddProjectIntent(title="Альфа", template_code="standard", deadline=date(2026, 6, 30))
    out = describe_intent(intent)
    assert "Альфа" in out
    assert "2026-06-30" in out


def test_describe_intent_load():
    out = describe_intent(LoadIntent(person_name="Андрей"))
    assert "Андрей" in out


def test_describe_intent_what_if():
    out = describe_intent(WhatIfIntent(operation="shift_deadline", project_title="Бета"))
    assert "Бета" in out


def test_describe_intent_vacation():
    out = describe_intent(
        VacationIntent(person_name="Айгуль", day_from=date(2026, 6, 10), day_to=date(2026, 6, 12))
    )
    assert "Айгуль" in out


def test_describe_intent_confirm():
    out = describe_intent(ConfirmIntent())
    assert "Подтверждение" in out


def test_describe_intent_assign():
    out = describe_intent(AssignIntent(task_ref="task-1", person_name="Андрей"))
    assert out  # any non-empty reply


def test_describe_intent_clarify_returns_question():
    out = describe_intent(ClarifyIntent(question="Уточни дату."))
    assert "Уточни дату." in out


# ---------------------------------------------------------------------------
# _handle_text — ClarifyIntent path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_text_clarify_replies_question():
    msg, answers = _message()
    intent = ClarifyIntent(question="Не понял — переформулируй.")
    await _handle_text(msg, "что угодно", _FakeParser(intent), {"is_admin": True})  # type: ignore[arg-type]
    assert "Не понял" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_text_write_op_blocked_for_non_admin():
    msg, answers = _message()
    intent = AddProjectIntent(title="X", template_code="standard", deadline=date(2026, 6, 30))
    await _handle_text(msg, "новый проект X", _FakeParser(intent), {"is_admin": False})  # type: ignore[arg-type]
    assert "Только админ" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_text_add_project_no_repo_echoes_intent():
    msg, answers = _message()
    intent = AddProjectIntent(title="Гамма", template_code="standard", deadline=date(2026, 6, 30))
    await _handle_text(msg, "...", _FakeParser(intent), {"is_admin": True}, repo=None)  # type: ignore[arg-type]
    assert answers.calls  # some reply


# ---------------------------------------------------------------------------
# handle_load
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_load_no_repo():
    msg, answers = _message("/load")
    parser = _FakeParser(LoadIntent())
    await load_handler.handle_load(msg, parser, repo=None)  # type: ignore[arg-type]
    assert "не подключён" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_load_no_people():
    msg, answers = _message("/load")
    parser = _FakeParser(LoadIntent())
    repo = _FakeRepo(people=())
    await load_handler.handle_load(msg, parser, repo=repo)  # type: ignore[arg-type]
    assert "нет активных" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_load_with_people_sends_photo():
    person = Person(id=uuid4(), name="Андрей", capacity_h=8)
    msg, answers = _message("/load")
    parser = _FakeParser(LoadIntent())
    repo = _FakeRepo(people=(person,), plans=[])
    await load_handler.handle_load(msg, parser, repo=repo)  # type: ignore[arg-type]
    assert answers.photos, "expected answer_photo call"
    assert "Андрей" in answers.photos[0][1] or "команда" in answers.photos[0][1]


# ---------------------------------------------------------------------------
# whatif._base_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_base_request_no_people_returns_none():
    repo = _FakeRepo(people=())
    solver = GreedySolver(WeekendCalendar())
    result = await whatif_handler._base_request(repo, solver)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_base_request_builds_plan_request():
    person = Person(id=uuid4(), name="Андрей", capacity_h=8)
    task_id = uuid4()
    plans = [
        {
            "assignments": [
                {
                    "task_id": str(task_id),
                    "person_id": str(person.id),
                    "allocations": [{"hours": 8}],
                }
            ]
        }
    ]
    repo = _FakeRepo(people=(person,), plans=plans)
    solver = GreedySolver(WeekendCalendar())
    req = await whatif_handler._base_request(repo, solver)  # type: ignore[arg-type]
    assert req is not None
    assert len(req.tasks) == 1


# ---------------------------------------------------------------------------
# handle_whatif with repo+solver
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_whatif_with_repo_returns_diff():
    person = Person(id=uuid4(), name="Андрей", capacity_h=8)
    task_id = uuid4()
    plans = [
        {
            "assignments": [
                {
                    "task_id": str(task_id),
                    "person_id": str(person.id),
                    "allocations": [{"hours": 8}],
                }
            ]
        }
    ]
    repo = _FakeRepo(people=(person,), plans=plans)
    solver = GreedySolver(WeekendCalendar())
    intent = WhatIfIntent(
        operation="shift_deadline", project_title="Альфа", new_deadline=date(2026, 7, 1)
    )
    msg, answers = _message("/whatif сдвинуть Альфу")
    parser = _FakeParser(intent)
    await whatif_handler.handle_whatif(
        msg, parser, {"is_admin": True}, repo=repo, solver=solver  # type: ignore[arg-type]
    )
    assert answers.calls
    assert "Что-если" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_whatif_no_repo_fallback():
    intent = WhatIfIntent(operation="add_person", project_title="Бета")
    msg, answers = _message("/whatif +человек в Бету")
    parser = _FakeParser(intent)
    await whatif_handler.handle_whatif(
        msg, parser, {"is_admin": True}, repo=None, solver=None  # type: ignore[arg-type]
    )
    assert "Бета" in answers.calls[0]


# ---------------------------------------------------------------------------
# handle_mention_or_dm — private chat path (no bot.get_me() needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_mention_private_chat_responds():
    from planner.bot.handlers.task_router import handle_mention_or_dm

    intent = ClarifyIntent(question="Не понял.")
    msg, answers = _message("загрузка", chat_type="private")
    parser = _FakeParser(intent)
    await handle_mention_or_dm(msg, parser, {"is_admin": False})  # type: ignore[arg-type]
    assert answers.calls


@pytest.mark.asyncio
async def test_handle_mention_group_without_mention_ignores():
    from planner.bot.handlers.task_router import handle_mention_or_dm

    bot_info = SimpleNamespace(username="planer_by_possstum_bot", id=12345)
    bot = SimpleNamespace(get_me=AsyncMock(return_value=bot_info))

    intent = ClarifyIntent(question="Не понял.")
    msg, answers = _message("мяу мяу", chat_type="supergroup")
    msg.bot = bot
    parser = _FakeParser(intent)
    await handle_mention_or_dm(msg, parser, {"is_admin": False})  # type: ignore[arg-type]
    assert not answers.calls  # bot must stay silent


@pytest.mark.asyncio
async def test_handle_mention_group_with_mention_responds():
    from planner.bot.handlers.task_router import handle_mention_or_dm

    bot_info = SimpleNamespace(username="planer_by_possstum_bot", id=12345)
    bot = SimpleNamespace(get_me=AsyncMock(return_value=bot_info))

    intent = ClarifyIntent(question="Не понял.")
    msg, answers = _message("@planer_by_possstum_bot загрузка", chat_type="supergroup")
    msg.bot = bot
    parser = _FakeParser(intent)
    await handle_mention_or_dm(msg, parser, {"is_admin": False})  # type: ignore[arg-type]
    assert answers.calls
