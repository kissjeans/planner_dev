"""Tests for bot handlers: start, confirm, vacation, whatif (spec section 8.1)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from planner.app.ports import PersonRecord
from planner.bot.handlers import confirm, start, vacation, whatif
from planner.domain.intent import (
    VacationIntent,
    WhatIfIntent,
)

# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------

class _Answers:
    """Collects answers from message.answer()."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, text: str, **kwargs: Any) -> None:
        self.calls.append(text)


def _message(text: str = "") -> tuple[SimpleNamespace, _Answers]:
    answers = _Answers()
    msg = SimpleNamespace(text=text, answer=answers)
    return msg, answers


class _FakeParser:
    def __init__(self, intent: Any) -> None:
        self._intent = intent

    async def parse(self, text: str, ctx: Any) -> Any:
        return self._intent


# ---------------------------------------------------------------------------
# /start handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_handler_replies():
    msg, answers = _message("/start")
    await start.handle_start(msg)  # type: ignore[arg-type]
    assert len(answers.calls) == 1
    assert "Привет" in answers.calls[0] or answers.calls[0]  # any reply


# ---------------------------------------------------------------------------
# /vacation handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vacation_empty_text_shows_help():
    msg, answers = _message("/vacation")
    parser = _FakeParser(None)
    await vacation.handle_vacation(msg, parser, {"is_admin": True})  # type: ignore[arg-type]
    assert "Укажи" in answers.calls[0]


@pytest.mark.asyncio
async def test_vacation_non_admin_blocked():
    intent = VacationIntent(
        person_name="Айгуль",
        day_from=date(2026, 6, 10),
        day_to=date(2026, 6, 12),
    )
    msg, answers = _message("/vacation Айгуль 10 12")
    parser = _FakeParser(intent)
    await vacation.handle_vacation(msg, parser, {"is_admin": False})  # type: ignore[arg-type]
    assert "Только админ" in answers.calls[0]


@pytest.mark.asyncio
async def test_vacation_no_repo_returns_fallback():
    intent = VacationIntent(
        person_name="Айгуль",
        day_from=date(2026, 6, 10),
        day_to=date(2026, 6, 12),
    )
    msg, answers = _message("/vacation Айгуль 10 12")
    parser = _FakeParser(intent)
    await vacation.handle_vacation(
        msg, parser, {"is_admin": True}, repo=None  # type: ignore[arg-type]
    )
    assert "не подключён" in answers.calls[0]


@pytest.mark.asyncio
async def test_vacation_wrong_intent_shows_format_hint():
    from planner.domain.intent import LoadIntent

    intent = LoadIntent()
    msg, answers = _message("/vacation что-то непонятное")
    parser = _FakeParser(intent)
    await vacation.handle_vacation(msg, parser, {"is_admin": True})  # type: ignore[arg-type]
    assert "Не понял" in answers.calls[0]


@pytest.mark.asyncio
async def test_vacation_with_repo_calls_use_case():
    from planner.app.ports import PersonRecord

    class _FakeRepo:
        def __init__(self) -> None:
            self.overrides: list = []
            self.audits: list = []

        async def get_person_by_name(self, name: str) -> PersonRecord | None:
            return PersonRecord(id=uuid4(), name=name, is_admin=False)

        async def upsert_day_override(self, *args: Any) -> None:
            self.overrides.append(args)

        async def add_audit(self, *args: Any) -> None:
            self.audits.append(args)

    intent = VacationIntent(
        person_name="Айгуль",
        day_from=date(2026, 6, 10),
        day_to=date(2026, 6, 10),
    )
    repo = _FakeRepo()
    actor_record = PersonRecord(id=uuid4(), name="Admin", is_admin=True)
    msg, answers = _message("/vacation Айгуль 10 июня")
    parser = _FakeParser(intent)
    await vacation.handle_vacation(
        msg,  # type: ignore[arg-type]
        parser,
        {"is_admin": True},
        repo=repo,  # type: ignore[arg-type]
        actor_record=actor_record,
    )
    assert "оформлен" in answers.calls[0]
    assert len(repo.overrides) == 1


# ---------------------------------------------------------------------------
# /whatif handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_whatif_empty_text_shows_help():
    msg, answers = _message("/whatif")
    parser = _FakeParser(None)
    await whatif.handle_whatif(msg, parser, {"is_admin": True})  # type: ignore[arg-type]
    assert "Опиши" in answers.calls[0]


@pytest.mark.asyncio
async def test_whatif_non_whatif_intent_returns_message():
    from planner.domain.intent import LoadIntent

    intent = LoadIntent()
    msg, answers = _message("/whatif что угодно")
    parser = _FakeParser(intent)
    await whatif.handle_whatif(msg, parser, {"is_admin": True})  # type: ignore[arg-type]
    assert "что-если" in answers.calls[0].lower()


@pytest.mark.asyncio
async def test_whatif_non_admin_blocked():
    intent = WhatIfIntent(operation="shift_deadline", project_title="Альфа")
    msg, answers = _message("/whatif сдвинуть Альфу")
    parser = _FakeParser(intent)
    await whatif.handle_whatif(msg, parser, {"is_admin": False})  # type: ignore[arg-type]
    assert "Только админ" in answers.calls[0]


@pytest.mark.asyncio
async def test_whatif_returns_operation_description():
    intent = WhatIfIntent(operation="shift_deadline", project_title="Альфа")
    msg, answers = _message("/whatif сдвинуть Альфу")
    parser = _FakeParser(intent)
    await whatif.handle_whatif(msg, parser, {"is_admin": True})  # type: ignore[arg-type]
    assert len(answers.calls) == 1


# ---------------------------------------------------------------------------
# confirm/edit callbacks
# ---------------------------------------------------------------------------

class _CbAnswers:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.data: str = ""

    async def answer(self, text: str = "", **kwargs: Any) -> None:
        self.calls.append(text)


def _callback(data: str) -> tuple[SimpleNamespace, _CbAnswers]:
    cb_answers = _CbAnswers()
    cb = SimpleNamespace(data=data, answer=cb_answers.answer)
    return cb, cb_answers


@pytest.mark.asyncio
async def test_confirm_callback_non_admin_blocked():
    pv_id = uuid4()
    cb, cb_answers = _callback(f"confirm:{pv_id}")
    await confirm.handle_confirm(cb, {"is_admin": False})  # type: ignore[arg-type]
    assert "Только админ" in cb_answers.calls[0]


@pytest.mark.asyncio
async def test_confirm_callback_no_repo_acknowledges():
    pv_id = uuid4()
    cb, cb_answers = _callback(f"confirm:{pv_id}")
    await confirm.handle_confirm(cb, {"is_admin": True})  # type: ignore[arg-type]
    assert cb_answers.calls[0]  # no-repo path returns some message


@pytest.mark.asyncio
async def test_confirm_callback_with_repo_calls_use_case():
    from planner.app.confirm_plan import PlanNotFoundError

    pv_id = uuid4()
    actor_record = PersonRecord(id=uuid4(), name="Admin", is_admin=True)

    class _FakeConfirmUC:
        async def execute(self, plan_version_id: Any, actor: Any) -> None:
            if plan_version_id != pv_id:
                raise PlanNotFoundError("not found")

    cb, cb_answers = _callback(f"confirm:{pv_id}")
    await confirm.handle_confirm(
        cb,  # type: ignore[arg-type]
        {"is_admin": True},
        confirm_uc=_FakeConfirmUC(),  # type: ignore[arg-type]
        actor_record=actor_record,
    )
    assert "зафиксирован" in cb_answers.calls[0].lower()


@pytest.mark.asyncio
async def test_edit_callback_returns_prompt():
    cb, cb_answers = _callback("edit:some-id")
    await confirm.handle_edit(cb)  # type: ignore[arg-type]
    assert len(cb_answers.calls) == 1
