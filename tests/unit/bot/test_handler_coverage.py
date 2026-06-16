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
    CaptureTaskIntent,
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
    def __init__(self, people=(), plans=(), deps=()) -> None:
        self._people = people
        self._plans = list(plans)
        self._deps = list(deps)
        self.captured_tasks: list[str] = []
        self.assignments: list[tuple] = []
        self.saved_tasks: list[tuple] = []

    async def get_solver_people(self) -> tuple:
        return self._people

    async def list_committed_plans(self) -> list:
        return self._plans

    async def get_task_name_map(self):
        return {}

    async def list_task_dependencies(self):
        return list(self._deps)

    async def get_project_template(self, code: str) -> None:
        return None

    # --- capture flow ---
    async def get_project_by_title(self, title):
        return None  # always create

    async def create_project(self, *, title, template_code, deadline,
                             brief_return_date, actor_id, project_id=None):
        from planner.app.ports import ProjectRecord
        return ProjectRecord(project_id or uuid4(), title, "planning", deadline)

    async def create_task(self, *, project_id, name, duration_hours, deadline, actor_id):
        from planner.app.ports import TaskRecord
        self.captured_tasks.append(name)
        return TaskRecord(id=uuid4(), name=name, status="not_done",
                          end_date=deadline, duration_hours=duration_hours)

    async def get_person_by_name(self, name):
        return None  # unknown → no assignment

    async def assign_task(self, task_id, person_id, hours):
        self.assignments.append((task_id, person_id, hours))

    async def list_people(self):
        return []

    async def list_projects(self):
        return []

    async def add_audit(self, *a):
        pass

    async def save_project_tasks(self, project_id, tasks, assignments) -> None:
        self.saved_tasks.append((project_id, tasks, assignments))


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


def test_describe_intent_capture_task():
    out = describe_intent(
        CaptureTaskIntent(task_title="сделать бриф", assignee_names=["Андрей"])
    )
    assert "сделать бриф" in out
    assert "Андрей" in out


# ---------------------------------------------------------------------------
# _handle_text — CaptureTaskIntent path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_text_capture_writes_to_db():
    from planner.app.ports import PersonRecord
    msg, answers = _message()
    repo = _FakeRepo()
    intent = CaptureTaskIntent(
        task_title="подготовить бриф", project_name="МТС", assignee_names=["Призрак"]
    )
    actor_record = PersonRecord(id=uuid4(), name="Андрей", is_admin=True)
    await _handle_text(
        msg, "подготовить бриф по мтс", _FakeParser(intent),  # type: ignore[arg-type]
        {"is_admin": True}, repo=repo, actor_record=actor_record,  # type: ignore[arg-type]
    )
    assert "Записал" in answers.calls[0]
    assert repo.captured_tasks == ["подготовить бриф"]
    assert repo.assignments == []


@pytest.mark.asyncio
async def test_handle_text_capture_non_admin_blocked():
    """capture_task is a write -> a known non-admin must be rejected, no DB write."""
    from planner.app.ports import PersonRecord
    msg, answers = _message()
    repo = _FakeRepo()
    intent = CaptureTaskIntent(task_title="подготовить бриф", project_name="МТС")
    actor_record = PersonRecord(id=uuid4(), name="Андрей", is_admin=False)
    await _handle_text(
        msg, "подготовить бриф по мтс", _FakeParser(intent),  # type: ignore[arg-type]
        {"is_admin": False}, repo=repo, actor_record=actor_record,  # type: ignore[arg-type]
    )
    assert "Только админ" in answers.calls[0]
    assert repo.captured_tasks == []


@pytest.mark.asyncio
async def test_whatif_base_request_preserves_dependencies():
    """plan 022: the reconstructed what-if baseline must keep real dependencies."""
    from planner.bot.handlers.whatif import _base_request
    from planner.domain.models import Dependency, Person

    a_id, b_id, p_id = uuid4(), uuid4(), uuid4()
    person = Person(id=p_id, name="P", capacity_h=8)
    payload = {
        "assignments": [
            {"task_id": str(a_id), "person_id": str(p_id), "allocations": [{"hours": 8}]},
            {"task_id": str(b_id), "person_id": str(p_id), "allocations": [{"hours": 8}]},
        ]
    }
    dep = Dependency(task_id=b_id, depends_on_id=a_id, link_type="FS")
    repo = _FakeRepo(people=(person,), plans=(payload,), deps=(dep,))
    req = await _base_request(repo, solver=None)  # type: ignore[arg-type]
    assert req is not None
    assert len(req.tasks) == 2
    assert req.dependencies == (dep,)


@pytest.mark.asyncio
async def test_handle_text_unknown_sender_blocked_no_write():
    msg, answers = _message()
    repo = _FakeRepo()
    intent = CaptureTaskIntent(task_title="запиши задачу")
    # No actor_record, not admin → unknown sender.
    await _handle_text(
        msg, "запиши задачу", _FakeParser(intent),  # type: ignore[arg-type]
        {"is_admin": False}, repo=repo,  # type: ignore[arg-type]
    )
    assert "Не узнал тебя" in answers.calls[0]
    assert repo.captured_tasks == []  # nothing written


@pytest.mark.asyncio
async def test_handle_text_capture_no_repo_echoes():
    msg, answers = _message()
    intent = CaptureTaskIntent(task_title="что-то")
    await _handle_text(
        msg, "что-то", _FakeParser(intent), {"is_admin": False}, repo=None  # type: ignore[arg-type]
    )
    assert answers.calls  # echoes describe_intent


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


# ---------------------------------------------------------------------------
# _plan_keyboard + full AddProject via _handle_text with repo+solver
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_text_add_project_with_repo_sends_keyboard():
    from datetime import timedelta

    from planner.app.add_project import ProjectTemplate, TemplateTaskSpec
    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import _handle_text
    from planner.domain.calendar.rules import WeekendCalendar
    from planner.domain.models import Person
    from planner.domain.solver.greedy import GreedySolver
    from tests.unit.app.conftest import FakeRepo

    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo = FakeRepo()
    repo.solver_people = (andrey,)
    repo.templates = {
        "standard": ProjectTemplate(
            code="standard",
            tasks=(TemplateTaskSpec(1, "Бриф", 8, (andrey.id,)),),
        )
    }

    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    solver = GreedySolver(WeekendCalendar())
    intent = AddProjectIntent(
        title="Тест", template_code="standard",
        deadline=date.today() + timedelta(days=30),
    )

    keyboards: list[Any] = []

    async def _answer(text: str, reply_markup: Any = None, **kw: Any) -> None:
        keyboards.append(reply_markup)

    msg = SimpleNamespace(
        text="/task Тест", answer=_answer,
        chat=SimpleNamespace(type="private"),
        reply_to_message=None, bot=None,
    )
    parser = _FakeParser(intent)
    await _handle_text(
        msg, "Тест", parser, {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=solver, actor_record=actor_record,
    )
    assert keyboards, "expected answer to be called"
    assert keyboards[0] is not None, "keyboard must be attached to proposed plan"


# ---------------------------------------------------------------------------
# handle_task command (/task <text>)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_task_empty_shows_help():
    from planner.bot.handlers.task_router import handle_task

    intent = ClarifyIntent(question="Не понял.")
    msg, answers = _message("/task")
    parser = _FakeParser(intent)
    await handle_task(msg, parser, {"is_admin": True})  # type: ignore[arg-type]
    assert "Напиши" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_task_with_text_routes_intent():
    from planner.bot.handlers.task_router import handle_task

    intent = ClarifyIntent(question="Уточни.")
    msg, answers = _message("/task загрузка")
    parser = _FakeParser(intent)
    await handle_task(msg, parser, {"is_admin": True})  # type: ignore[arg-type]
    assert answers.calls


# ---------------------------------------------------------------------------
# handle_edit_text FSM handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_edit_text_routes_and_clears_state():
    from planner.bot.handlers.task_router import handle_edit_text

    intent = ClarifyIntent(question="Не понял.")
    msg, answers = _message("правка: lite")
    parser = _FakeParser(intent)
    state = SimpleNamespace(clear=AsyncMock())
    await handle_edit_text(
        msg, state, parser, {"is_admin": True}  # type: ignore[arg-type]
    )
    assert answers.calls
    assert state.clear.called


@pytest.mark.asyncio
async def test_handle_edit_text_empty_message_ignored():
    from planner.bot.handlers.task_router import handle_edit_text

    intent = ClarifyIntent(question="X")
    msg, answers = _message("")
    parser = _FakeParser(intent)
    state = SimpleNamespace(clear=AsyncMock())
    await handle_edit_text(msg, state, parser, {"is_admin": True})  # type: ignore[arg-type]
    assert not answers.calls
    assert not state.clear.called


# ---------------------------------------------------------------------------
# handle_voice with STT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_voice_with_stt_transcribes_and_routes():
    from planner.bot.handlers.task_router import handle_voice

    intent = ClarifyIntent(question="Не понял.")
    msg, answers = _message()
    parser = _FakeParser(intent)

    audio_bytes = b"fake-audio"
    audio_buf = SimpleNamespace(read=lambda: audio_bytes)
    file_obj = SimpleNamespace(file_path="voice/file.ogg")
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=file_obj),
        download_file=AsyncMock(return_value=audio_buf),
    )
    msg.voice = SimpleNamespace(file_id="abc", file_size=1000)
    msg.bot = bot

    stt = SimpleNamespace(transcribe=AsyncMock(return_value="загрузка команды"))
    await handle_voice(
        msg, parser, {"is_admin": False}, stt=stt  # type: ignore[arg-type]
    )
    assert answers.calls


@pytest.mark.asyncio
async def test_handle_voice_stt_returns_empty_string():
    from planner.bot.handlers.task_router import handle_voice

    intent = ClarifyIntent(question="X")
    msg, answers = _message()
    parser = _FakeParser(intent)

    audio_buf = SimpleNamespace(read=lambda: b"")
    file_obj = SimpleNamespace(file_path="voice/f.ogg")
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=file_obj),
        download_file=AsyncMock(return_value=audio_buf),
    )
    msg.voice = SimpleNamespace(file_id="abc", file_size=1000)
    msg.bot = bot

    stt = SimpleNamespace(transcribe=AsyncMock(return_value=""))
    await handle_voice(
        msg, parser, {"is_admin": False}, stt=stt  # type: ignore[arg-type]
    )
    # ack is calls[0]; error reply is calls[1] (new ack+timeout flow)
    assert any("распознать" in c for c in answers.calls)


@pytest.mark.asyncio
async def test_handle_voice_no_stt_replies_unsupported():
    """task_router.py:166-167 — stt is None → early return with hint."""
    from planner.bot.handlers.task_router import handle_voice
    intent = ClarifyIntent(question="X")
    msg, answers = _message()
    msg.voice = SimpleNamespace(file_id="abc")
    parser = _FakeParser(intent)
    await handle_voice(msg, parser, {"is_admin": False})  # stt defaults to None
    assert "напиши текстом" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_voice_rejects_oversized():
    from planner.bot.handlers.task_router import handle_voice
    intent = ClarifyIntent(question="X")
    msg, answers = _message()
    msg.voice = SimpleNamespace(file_id="abc", file_size=50 * 1024 * 1024)  # 50 MB
    msg.bot = SimpleNamespace()  # must not be used — size check is first
    stt = SimpleNamespace(transcribe=AsyncMock())
    await handle_voice(msg, _FakeParser(intent), {"is_admin": False}, stt=stt)  # type: ignore[arg-type]
    assert "слишком большое" in answers.calls[0]
    assert not stt.transcribe.called


@pytest.mark.asyncio
async def test_handle_voice_missing_file_path():
    from planner.bot.handlers.task_router import handle_voice
    intent = ClarifyIntent(question="X")
    msg, answers = _message()
    msg.voice = SimpleNamespace(file_id="abc", file_size=1000)
    file_obj = SimpleNamespace(file_path=None)
    msg.bot = SimpleNamespace(get_file=AsyncMock(return_value=file_obj))
    stt = SimpleNamespace(transcribe=AsyncMock())
    await handle_voice(msg, _FakeParser(intent), {"is_admin": False}, stt=stt)  # type: ignore[arg-type]
    assert "Не удалось получить" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_voice_sends_ack():
    """User gets a '🎙 Распознаю…' ack before transcription completes."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from planner.bot.handlers.task_router import handle_voice

    sent = []
    ack = SimpleNamespace(delete=AsyncMock())
    msg = SimpleNamespace(
        voice=SimpleNamespace(file_size=100, file_id="f"),
        bot=SimpleNamespace(
            get_file=AsyncMock(return_value=SimpleNamespace(file_path="p")),
            download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"x")),
        ),
        answer=AsyncMock(side_effect=lambda *a, **k: (sent.append(a[0]), ack)[1]),
    )

    class _P:  # parser; capture path not exercised here
        async def parse(self, text, ctx):
            from planner.domain.intent import ClarifyIntent
            return ClarifyIntent(question="x")

    stt = SimpleNamespace(transcribe=AsyncMock(return_value="загрузка"))
    await handle_voice(msg, _P(), {"is_admin": False}, stt=stt)  # type: ignore[arg-type]
    assert any("Распозна" in s for s in sent)


@pytest.mark.asyncio
async def test_handle_voice_timeout_replies(monkeypatch):
    """A slow transcription times out and tells the user, not hangs."""
    import asyncio as _aio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from planner.bot.handlers import task_router

    monkeypatch.setattr(task_router, "_STT_TIMEOUT_S", 0.01)
    sent = []
    ack = SimpleNamespace(delete=AsyncMock())
    msg = SimpleNamespace(
        voice=SimpleNamespace(file_size=100, file_id="f"),
        bot=SimpleNamespace(
            get_file=AsyncMock(return_value=SimpleNamespace(file_path="p")),
            download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"x")),
        ),
        answer=AsyncMock(side_effect=lambda *a, **k: (sent.append(a[0]), ack)[1]),
    )

    async def _slow(*a, **k):
        await _aio.sleep(1)
        return "never"

    stt = SimpleNamespace(transcribe=_slow)
    await task_router.handle_voice(msg, object(), {"is_admin": False}, stt=stt)  # type: ignore[arg-type]
    assert any("Долго распознаю" in s for s in sent)


@pytest.mark.asyncio
async def test_handle_mention_only_botname_no_text_ignored():
    """task_router.py:263 — message is '@bot' with nothing after → return."""
    from planner.bot.handlers.task_router import handle_mention_or_dm
    intent = ClarifyIntent(question="X")
    msg, answers = _message()
    msg.text = "@plannerbot"  # stripped → empty text
    msg.chat = SimpleNamespace(type="private")
    parser = _FakeParser(intent)
    await handle_mention_or_dm(msg, parser, {"is_admin": False})  # type: ignore[arg-type]
    assert len(answers.calls) == 0  # no reply


# ---------------------------------------------------------------------------
# handle_edit_text — supersede old proposal on successful re-plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_text_populates_context_from_repo():
    """_handle_text must pass known_people/known_projects to the parser."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from planner.bot.handlers.task_router import _handle_text
    from planner.domain.intent import LoadIntent

    captured = {}

    class _Parser:
        async def parse(self, text, ctx):
            captured["ctx"] = ctx
            return LoadIntent(person_name=None)

    repo = SimpleNamespace(
        list_people=AsyncMock(return_value=[SimpleNamespace(id=uuid4(), name="Рай")]),
        list_projects=AsyncMock(return_value=[SimpleNamespace(id=uuid4(), title="МТС")]),
    )
    msg = SimpleNamespace(answer=AsyncMock())
    await _handle_text(
        msg, "сколько слотов у Рая?", _Parser(), {"is_admin": True},
        repo=repo, actor_record=SimpleNamespace(id=uuid4(), name="Андрей"),
    )
    assert "Рай" in captured["ctx"].known_people
    assert "МТС" in captured["ctx"].known_projects


@pytest.mark.asyncio
async def test_handle_edit_text_supersedes_old_proposal():
    from datetime import timedelta

    from planner.app.add_project import ProjectTemplate, TemplateTaskSpec
    from planner.app.ports import PersonRecord, PlanVersionRecord, ProjectRecord
    from planner.bot.handlers.task_router import handle_edit_text
    from planner.domain.calendar.rules import WeekendCalendar
    from planner.domain.models import Person
    from planner.domain.solver.greedy import GreedySolver
    from tests.unit.app.conftest import FakeRepo

    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo = FakeRepo()
    repo.solver_people = (andrey,)
    repo.templates = {
        "standard": ProjectTemplate(
            code="standard", tasks=(TemplateTaskSpec(1, "Бриф", 8, (andrey.id,)),)
        )
    }
    # Pre-existing proposed plan + its project (the one being edited).
    old_project_id = uuid4()
    repo.projects[old_project_id] = ProjectRecord(old_project_id, "Старый", "planning", None)
    old_pv = PlanVersionRecord(uuid4(), old_project_id, "proposed", {})
    repo.plan_versions[old_pv.id] = old_pv

    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    intent = AddProjectIntent(
        title="Новый", template_code="standard",
        deadline=date.today() + timedelta(days=30),
    )
    msg, answers = _message("правка: новый план")
    state = SimpleNamespace(
        clear=AsyncMock(),
        get_data=AsyncMock(return_value={"pending_pv_id": str(old_pv.id)}),
    )
    await handle_edit_text(
        msg, state, _FakeParser(intent), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=GreedySolver(WeekendCalendar()), actor_record=actor_record,
    )
    assert repo.plan_versions[old_pv.id].status == "superseded"
    assert repo.projects[old_project_id].status == "cancelled"
    assert state.clear.called
