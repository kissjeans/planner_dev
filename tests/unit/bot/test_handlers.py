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
async def test_whatif_open_to_non_admin():
    # spec section 16: what-if is read-only -> a non-admin may run it.
    intent = WhatIfIntent(operation="shift_deadline", project_title="Альфа")
    msg, answers = _message("/whatif сдвинуть Альфу")
    parser = _FakeParser(intent)
    await whatif.handle_whatif(msg, parser, {"is_admin": False})  # type: ignore[arg-type]
    assert "Только админ" not in answers.calls[0]
    assert "Альфа" in answers.calls[0]


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
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    cb, cb_answers = _callback("edit:some-id")
    msg = SimpleNamespace(answer=cb_answers.answer)
    cb.message = msg  # type: ignore[attr-defined]

    state = SimpleNamespace(
        set_state=AsyncMock(),
        update_data=AsyncMock(),
    )
    await confirm.handle_edit(cb, state)  # type: ignore[arg-type]
    assert state.set_state.called
    assert len(cb_answers.calls) >= 1


# ---------------------------------------------------------------------------
# vacation — PersonNotFoundError + PermissionError paths (lines 56-61)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirm_callback_plan_not_proposed():
    from planner.app.confirm_plan import PlanNotProposedError

    pv_id = uuid4()
    actor_record = PersonRecord(id=uuid4(), name="Admin", is_admin=True)

    class _FakeConfirmUCNotProposed:
        async def execute(self, plan_version_id: Any, actor: Any) -> None:
            raise PlanNotProposedError("already committed")

    cb, cb_answers = _callback(f"confirm:{pv_id}")
    await confirm.handle_confirm(
        cb,  # type: ignore[arg-type]
        {"is_admin": True},
        confirm_uc=_FakeConfirmUCNotProposed(),  # type: ignore[arg-type]
        actor_record=actor_record,
    )
    reply = cb_answers.calls[0].lower()
    assert "не найден" in reply or "зафиксирован" in reply


@pytest.mark.asyncio
async def test_vacation_permission_error_message():

    class _RepoPermError:
        async def get_person_by_name(self, name: str):
            from planner.app.ports import PersonRecord
            return PersonRecord(id=uuid4(), name=name, is_admin=False)

        async def upsert_day_override(self, *a):
            raise PermissionError("не разрешено")

        async def add_audit(self, *a): pass

    intent = VacationIntent(
        person_name="Айгуль",
        day_from=date(2026, 6, 10),
        day_to=date(2026, 6, 10),
    )
    actor_record = PersonRecord(id=uuid4(), name="Admin", is_admin=True)
    msg, answers = _message("/vacation Айгуль 10 июня")
    parser = _FakeParser(intent)
    await vacation.handle_vacation(
        msg, parser, {"is_admin": True},  # type: ignore[arg-type]
        repo=_RepoPermError(), actor_record=actor_record,  # type: ignore[arg-type]
    )
    assert answers.calls


@pytest.mark.asyncio
async def test_vacation_person_not_found_message():

    class _RepoNotFound:
        async def get_person_by_name(self, name: str):
            return None
        async def upsert_day_override(self, *a): pass
        async def add_audit(self, *a): pass

    intent = VacationIntent(
        person_name="Призрак",
        day_from=date(2026, 6, 10),
        day_to=date(2026, 6, 10),
    )
    actor_record = PersonRecord(id=uuid4(), name="Admin", is_admin=True)
    msg, answers = _message("/vacation Призрак 10 июня")
    parser = _FakeParser(intent)
    await vacation.handle_vacation(
        msg, parser, {"is_admin": True},  # type: ignore[arg-type]
        repo=_RepoNotFound(), actor_record=actor_record,  # type: ignore[arg-type]
    )
    assert "не найден" in answers.calls[0]


# ---------------------------------------------------------------------------
# /whatif switch_to_lite (real scope reduction — Cluster G / spec §6)
# ---------------------------------------------------------------------------

class _WhatIfLiteRepo:
    """Fake repo that exposes a committed full plan and a smaller lite template.

    Read-only: any write attempt would raise AttributeError (methods absent).
    """

    def __init__(self, *, lite_template: Any, n_committed_tasks: int = 3) -> None:
        from planner.domain.models import Person

        self._person = Person(id=uuid4(), name="Андрей", capacity_h=8)
        self._lite_template = lite_template
        self._task_ids = [uuid4() for _ in range(n_committed_tasks)]

    async def get_solver_people(self) -> tuple[Any, ...]:
        return (self._person,)

    async def list_committed_plans(self) -> list[dict[str, Any]]:
        return [
            {
                "assignments": [
                    {
                        "task_id": str(tid),
                        "person_id": str(self._person.id),
                        "allocations": [
                            {
                                "person_id": str(self._person.id),
                                "day": "2026-06-02",
                                "hours": 8,
                            }
                        ],
                    }
                    for tid in self._task_ids
                ]
            }
        ]

    async def get_task_name_map(self) -> dict[Any, str]:
        return {tid: f"task-{i}" for i, tid in enumerate(self._task_ids)}

    async def list_task_dependencies(self) -> list[Any]:
        return []

    async def get_project_template(self, code: str) -> Any:
        return self._lite_template if code == "lite" else None


def _lite_template(n_tasks: int = 1):
    from planner.app.add_project import ProjectTemplate, TemplateTaskSpec

    return ProjectTemplate(
        code="lite",
        tasks=tuple(
            TemplateTaskSpec(
                ord=i,
                name=f"lite-{i}",
                duration_hours=8,
                allowed_person_ids=(),
            )
            for i in range(n_tasks)
        ),
    )


@pytest.mark.asyncio
async def test_whatif_switch_to_lite_reduces_scope():
    from planner.domain.calendar.rules import WeekendCalendar
    from planner.domain.solver.greedy import GreedySolver

    repo = _WhatIfLiteRepo(lite_template=_lite_template(n_tasks=1), n_committed_tasks=3)
    solver = GreedySolver(WeekendCalendar())
    intent = WhatIfIntent(operation="switch_to_lite", project_title="Альфа")
    msg, answers = _message("/whatif lite Альфа")
    parser = _FakeParser(intent)

    await whatif.handle_whatif(
        msg,  # type: ignore[arg-type]
        parser,
        {"is_admin": False},
        repo=repo,  # type: ignore[arg-type]
        solver=solver,  # type: ignore[arg-type]
    )

    reply = answers.calls[0]
    # Real reduction: 3 committed tasks -> 1 lite task. Message must convey it.
    assert "3" in reply and "1" in reply
    assert "не могу сопоставить" not in reply


@pytest.mark.asyncio
async def test_whatif_switch_to_lite_missing_template_friendly_message():
    from planner.domain.calendar.rules import WeekendCalendar
    from planner.domain.solver.greedy import GreedySolver

    repo = _WhatIfLiteRepo(lite_template=None, n_committed_tasks=2)
    solver = GreedySolver(WeekendCalendar())
    intent = WhatIfIntent(operation="switch_to_lite", project_title="Альфа")
    msg, answers = _message("/whatif lite Альфа")
    parser = _FakeParser(intent)

    await whatif.handle_whatif(
        msg,  # type: ignore[arg-type]
        parser,
        {"is_admin": False},
        repo=repo,  # type: ignore[arg-type]
        solver=solver,  # type: ignore[arg-type]
    )

    assert "не могу сопоставить lite-шаблон" in answers.calls[0].lower()
