"""Coverage tests for load and task_router handlers (uncovered paths)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from planner.bot.handlers import load as load_handler
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


def _message(
    text: str = "", chat_type: str = "private", chat_id: int = 42
) -> tuple[SimpleNamespace, _Answers]:
    answers = _Answers()
    chat = SimpleNamespace(type=chat_type, id=chat_id)
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

    async def parse_intents(self, text: str, ctx: Any) -> list[Any]:
        return [self._intent]


class _CountingParser(_FakeParser):
    """Parser double that records how many times parse() was invoked."""

    def __init__(self, intent: Any) -> None:
        super().__init__(intent)
        self.parse_calls = 0

    async def parse(self, text: str, ctx: Any) -> Any:
        self.parse_calls += 1
        return self._intent


class _MultiParser:
    """Parser double returning several intents for a compound message."""

    def __init__(self, intents: list[Any]) -> None:
        self._intents = intents

    async def parse(self, text: str, ctx: Any) -> Any:
        return self._intents[0]

    async def parse_intents(self, text: str, ctx: Any) -> list[Any]:
        return list(self._intents)


class _FakeRepo:
    async def list_project_tasks(self, project_id):
        return []  # dedupe guard: no pre-existing tasks in these fakes

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

    async def create_task(self, *, project_id, name, duration_hours, deadline,
                          time_start=None, actor_id=None, required_skills=None):
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


class _ConfirmRepo:
    """Minimal RepoPort double for the typed-confirm path (L4)."""

    def __init__(self) -> None:
        from planner.app.ports import PlanVersionRecord
        self.plan_versions: dict[Any, PlanVersionRecord] = {}
        self.audits: list[tuple] = []

    async def get_plan_version(self, pv_id):  # type: ignore[no-untyped-def]
        return self.plan_versions.get(pv_id)

    async def transition_plan_status(self, pv_id, from_status, to_status) -> bool:  # type: ignore[no-untyped-def]
        from planner.app.ports import PlanVersionRecord
        pv = self.plan_versions.get(pv_id)
        if pv is None or pv.status != from_status:
            return False
        self.plan_versions[pv_id] = PlanVersionRecord(
            pv.id, pv.project_id, to_status, pv.payload
        )
        return True

    async def add_audit(self, *a) -> None:  # type: ignore[no-untyped-def]
        self.audits.append(a)

    async def set_project_status(self, project_id, status) -> None:  # type: ignore[no-untyped-def]
        self.project_statuses = getattr(self, "project_statuses", {})
        self.project_statuses[project_id] = status

    async def list_people(self):  # type: ignore[no-untyped-def]
        return []

    async def list_projects(self):  # type: ignore[no-untyped-def]
        return []


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
    await load_handler.handle_load(msg, parser, {"is_admin": False}, repo=None)  # type: ignore[arg-type]
    assert "не подключён" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_load_no_people():
    msg, answers = _message("/load")
    parser = _FakeParser(LoadIntent())
    repo = _FakeRepo(people=())
    await load_handler.handle_load(msg, parser, {"is_admin": True}, repo=repo)  # type: ignore[arg-type]
    assert "нет активных" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_load_with_people_sends_photo():
    person = Person(id=uuid4(), name="Андрей", capacity_h=8)
    msg, answers = _message("/load")
    parser = _FakeParser(LoadIntent())
    repo = _FakeRepo(people=(person,), plans=[])
    await load_handler.handle_load(msg, parser, {"is_admin": True}, repo=repo)  # type: ignore[arg-type]
    assert answers.photos, "expected answer_photo call"
    assert "Андрей" in answers.photos[0][1] or "команда" in answers.photos[0][1]


@pytest.mark.asyncio
async def test_handle_load_stranger_blocked_without_parse():
    person = Person(id=uuid4(), name="Андрей", capacity_h=8)
    msg, answers = _message("/load")
    parser = _CountingParser(LoadIntent())
    repo = _FakeRepo(people=(person,), plans=[])
    await load_handler.handle_load(
        msg, parser, {"is_admin": False}, repo=repo  # type: ignore[arg-type]
    )
    assert parser.parse_calls == 0, "stranger must not spend an LLM parse call"
    assert not answers.photos, "stranger must not get the heatmap"
    assert "Не узнал тебя" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_load_known_person_gets_heatmap():
    from planner.app.ports import PersonRecord

    person = Person(id=uuid4(), name="Андрей", capacity_h=8)
    msg, answers = _message("/load")
    parser = _FakeParser(LoadIntent())
    repo = _FakeRepo(people=(person,), plans=[])
    record = PersonRecord(id=uuid4(), name="Андрей", is_admin=False)
    await load_handler.handle_load(
        msg, parser, {"is_admin": False}, repo=repo, actor_record=record  # type: ignore[arg-type]
    )
    assert answers.photos, "expected answer_photo call"


# ---------------------------------------------------------------------------
# handle_mention_or_dm — private chat path (no bot.me() needed)
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

    # Only the cached bot.me() may be used — never the raw get_me() API call.
    bot_info = SimpleNamespace(username="planer_by_possstum_bot", id=12345)
    bot = SimpleNamespace(me=AsyncMock(return_value=bot_info))

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
    bot = SimpleNamespace(me=AsyncMock(return_value=bot_info))

    intent = ClarifyIntent(question="Не понял.")
    msg, answers = _message("@planer_by_possstum_bot загрузка", chat_type="supergroup")
    msg.bot = bot
    parser = _FakeParser(intent)
    await handle_mention_or_dm(msg, parser, {"is_admin": False})  # type: ignore[arg-type]
    assert answers.calls


@pytest.mark.asyncio
async def test_handle_mention_group_bare_mention_replies_hint():
    """A bare @mention (no text) must not be silent: it arms the voice window
    and tells the user what the bot expects (live UX gap, work chat 2026-07-07)."""
    from planner.bot.handlers.task_router import handle_mention_or_dm
    from planner.infra.voice_arm import VoiceArm

    bot_info = SimpleNamespace(username="planer_by_possstum_bot", id=12345)
    bot = SimpleNamespace(me=AsyncMock(return_value=bot_info))

    parser = _FakeParser(ClarifyIntent(question="Не понял."))
    msg, answers = _message("@planer_by_possstum_bot", chat_type="supergroup")
    msg.bot = bot
    msg.from_user = SimpleNamespace(id=777)
    arm = VoiceArm()
    await handle_mention_or_dm(
        msg, parser, {"is_admin": False}, voice_arm=arm  # type: ignore[arg-type]
    )
    assert answers.calls, "bare mention was silently ignored"
    assert "голосовое" in answers.calls[0].lower()
    assert arm.is_armed(msg.chat.id, msg.from_user.id), "voice window not armed"


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
async def test_handle_edit_text_unparsed_keeps_loop_armed():
    """M7: a non-proposal, non-confirm reply (clarify) inside the edit loop
    must NOT clear the state — the manager can retry their edit."""
    from planner.bot.handlers.task_router import handle_edit_text

    intent = ClarifyIntent(question="Не понял.")
    msg, answers = _message("правка: lite")
    parser = _FakeParser(intent)
    state = SimpleNamespace(
        clear=AsyncMock(),
        set_state=AsyncMock(),
        update_data=AsyncMock(),
        get_data=AsyncMock(return_value={}),
    )
    await handle_edit_text(
        msg, state, parser, {"is_admin": True}  # type: ignore[arg-type]
    )
    assert answers.calls
    assert not state.clear.called  # loop stays armed
    assert not state.set_state.called  # no fresh proposal to re-arm on


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

        async def parse_intents(self, text, ctx):
            return [await self.parse(text, ctx)]

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
async def test_handle_mention_only_botname_no_text_hints():
    """Message is '@bot' with nothing after → hint reply, no parser call."""
    from planner.bot.handlers.task_router import handle_mention_or_dm
    intent = ClarifyIntent(question="X")
    msg, answers = _message()
    msg.text = "@plannerbot"  # stripped → empty text
    msg.chat = SimpleNamespace(type="private")
    parser = _FakeParser(intent)
    await handle_mention_or_dm(msg, parser, {"is_admin": False})  # type: ignore[arg-type]
    assert len(answers.calls) == 1
    assert "Слушаю" in answers.calls[0]


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

        async def parse_intents(self, text, ctx):
            return [await self.parse(text, ctx)]

    repo = SimpleNamespace(
        list_people=AsyncMock(return_value=[SimpleNamespace(id=uuid4(), name="Рай")]),
        list_projects=AsyncMock(return_value=[SimpleNamespace(id=uuid4(), title="МТС")]),
        get_solver_people=AsyncMock(return_value=[]),
        list_committed_plans=AsyncMock(return_value=[]),
    )
    msg = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
    await _handle_text(
        msg, "сколько слотов у Рая?", _Parser(), {"is_admin": True},
        repo=repo, actor_record=SimpleNamespace(id=uuid4(), name="Андрей"),
    )
    assert "Рай" in captured["ctx"].known_people
    assert "МТС" in captured["ctx"].known_projects


@pytest.mark.asyncio
async def test_handle_text_passes_recent_messages_from_history():
    """A prior recorded turn must reach the parser via ctx.recent_messages,
    and the current message must be recorded for the next turn."""
    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import _handle_text
    from planner.infra.history import ChatHistory

    captured: dict[str, Any] = {}

    class _Parser:
        async def parse(self, text: str, ctx: Any) -> Any:
            captured["ctx"] = ctx
            return ClarifyIntent(question="Не понял.")

        async def parse_intents(self, text: str, ctx: Any) -> Any:
            return [await self.parse(text, ctx)]

    history = ChatHistory()
    history.record(42, "поставь задачу на МТС")  # earlier turn in this chat

    msg, _ = _message("тогда ставь на Андрея", chat_id=42)
    repo = _FakeRepo()
    actor_record = PersonRecord(id=uuid4(), name="Андрей", is_admin=True)
    await _handle_text(
        msg, "тогда ставь на Андрея", _Parser(), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, actor_record=actor_record, history=history,  # type: ignore[arg-type]
    )
    # (a) the prior turn reached the parser
    assert captured["ctx"].recent_messages == ("поставь задачу на МТС",)
    # (b) the current message was recorded (after capturing recent), so it is
    # available to the *next* turn but absent from its own context.
    assert history.recent(42) == ("поставь задачу на МТС", "тогда ставь на Андрея")


@pytest.mark.asyncio
async def test_handle_text_without_history_passes_empty_recent():
    """history=None (existing call sites) leaves recent_messages empty."""
    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import _handle_text

    captured: dict[str, Any] = {}

    class _Parser:
        async def parse(self, text: str, ctx: Any) -> Any:
            captured["ctx"] = ctx
            return ClarifyIntent(question="Не понял.")

        async def parse_intents(self, text: str, ctx: Any) -> Any:
            return [await self.parse(text, ctx)]

    msg, _ = _message("привет")
    actor_record = PersonRecord(id=uuid4(), name="Андрей", is_admin=True)
    await _handle_text(
        msg, "привет", _Parser(), {"is_admin": True},  # type: ignore[arg-type]
        repo=_FakeRepo(), actor_record=actor_record, history=None,  # type: ignore[arg-type]
    )
    assert captured["ctx"].recent_messages == ()


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
        set_state=AsyncMock(),
        update_data=AsyncMock(),
        get_data=AsyncMock(return_value={"pending_pv_id": str(old_pv.id)}),
    )
    await handle_edit_text(
        msg, state, _FakeParser(intent), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=GreedySolver(WeekendCalendar()), actor_record=actor_record,
    )
    assert repo.plan_versions[old_pv.id].status == "superseded"
    assert repo.projects[old_project_id].status == "cancelled"
    # M7: a fresh proposal re-arms the edit loop (does NOT clear) so the
    # manager can keep editing; state is cleared only on confirm / cancel.
    assert not state.clear.called
    assert state.set_state.called
    # The new pending_pv_id must point at the freshly proposed plan.
    new_pv_id = next(
        pv.id for pv in repo.plan_versions.values() if pv.status == "proposed"
    )
    state.update_data.assert_awaited_with(pending_pv_id=str(new_pv_id))


@pytest.mark.asyncio
async def test_handle_edit_text_two_sequential_edits_both_apply():
    """M7: edits ACCUMULATE — two sequential edits each produce a fresh proposal
    and the loop stays armed in between (only confirm/cancel clears it)."""
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
    first_project_id = uuid4()
    repo.projects[first_project_id] = ProjectRecord(
        first_project_id, "Старый", "planning", None
    )
    first_pv = PlanVersionRecord(uuid4(), first_project_id, "proposed", {})
    repo.plan_versions[first_pv.id] = first_pv

    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    solver = GreedySolver(WeekendCalendar())

    # FSM state backed by a simple dict so re-arm carries between edits.
    fsm: dict[str, Any] = {"pending_pv_id": str(first_pv.id)}

    class _State:
        def __init__(self) -> None:
            self.cleared = False

        async def get_data(self) -> dict[str, Any]:
            return dict(fsm)

        async def update_data(self, **kw: Any) -> None:
            fsm.update(kw)

        async def set_state(self, _state: Any) -> None:
            pass

        async def clear(self) -> None:
            self.cleared = True

    state = _State()

    intent_a = AddProjectIntent(
        title="Правка A", template_code="standard",
        deadline=date.today() + timedelta(days=30),
    )
    msg, _ = _message("правка A")
    await handle_edit_text(
        msg, state, _FakeParser(intent_a), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=solver, actor_record=actor_record,
    )
    assert repo.plan_versions[first_pv.id].status == "superseded"
    second_pv_id = UUID(fsm["pending_pv_id"])
    assert second_pv_id != first_pv.id
    assert repo.plan_versions[second_pv_id].status == "proposed"
    assert not state.cleared  # still armed

    # Second edit: must supersede the second proposal and arm a third.
    intent_b = AddProjectIntent(
        title="Правка B", template_code="standard",
        deadline=date.today() + timedelta(days=30),
    )
    msg2, _ = _message("правка B")
    await handle_edit_text(
        msg2, state, _FakeParser(intent_b), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=solver, actor_record=actor_record,
    )
    assert repo.plan_versions[second_pv_id].status == "superseded"
    third_pv_id = UUID(fsm["pending_pv_id"])
    assert third_pv_id not in (first_pv.id, second_pv_id)
    assert repo.plan_versions[third_pv_id].status == "proposed"
    assert not state.cleared


# ---------------------------------------------------------------------------
# L4: typed confirm commits the latest proposed plan via _handle_text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_text_confirm_commits_via_last_pv_id():
    """A ConfirmIntent (no pv_id) commits the latest proposed plan from context."""
    from planner.app.ports import PersonRecord, PlanVersionRecord

    repo = _ConfirmRepo()
    pv = PlanVersionRecord(uuid4(), uuid4(), "proposed", {})
    repo.plan_versions[pv.id] = pv
    from planner.app.confirm_plan import ConfirmPlanUseCase
    confirm_uc = ConfirmPlanUseCase(repo)  # type: ignore[arg-type]
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)

    msg, answers = _message()
    await _handle_text(
        msg, "ок", _FakeParser(ConfirmIntent()), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, actor_record=actor_record,  # type: ignore[arg-type]
        confirm_uc=confirm_uc, last_pv_id=pv.id,
    )
    assert repo.plan_versions[pv.id].status == "committed"
    assert "зафиксирован" in answers.calls[0].lower()


@pytest.mark.asyncio
async def test_handle_text_confirm_warns_on_partial_mirror():
    """A typed «ок» reports a partial Notion mirror, same as the inline button."""
    from planner.app.confirm_plan import ConfirmResult
    from planner.app.ports import PersonRecord, PlanVersionRecord

    pv = PlanVersionRecord(uuid4(), uuid4(), "committed", {})

    class _PartialMirrorUC:
        async def execute(self, pv_id, actor):
            return ConfirmResult(plan=pv, mirror_total=5, mirror_failed=2)

    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    msg, answers = _message()
    await _handle_text(
        msg, "ок", _FakeParser(ConfirmIntent()), {"is_admin": True},  # type: ignore[arg-type]
        repo=_ConfirmRepo(), actor_record=actor_record,  # type: ignore[arg-type]
        confirm_uc=_PartialMirrorUC(), last_pv_id=pv.id,  # type: ignore[arg-type]
    )
    reply = answers.calls[0]
    assert "зафиксирован" in reply.lower()
    assert "не удалось отразить 2 из 5" in reply


@pytest.mark.asyncio
async def test_handle_text_confirm_uses_explicit_plan_version_id():
    """ConfirmIntent.plan_version_id wins over context last_pv_id."""
    from planner.app.confirm_plan import ConfirmPlanUseCase
    from planner.app.ports import PersonRecord, PlanVersionRecord

    repo = _ConfirmRepo()
    pv = PlanVersionRecord(uuid4(), uuid4(), "proposed", {})
    repo.plan_versions[pv.id] = pv
    confirm_uc = ConfirmPlanUseCase(repo)  # type: ignore[arg-type]
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)

    msg, answers = _message()
    await _handle_text(
        msg, "ок", _FakeParser(ConfirmIntent(plan_version_id=pv.id)),  # type: ignore[arg-type]
        {"is_admin": True}, repo=repo, actor_record=actor_record,  # type: ignore[arg-type]
        confirm_uc=confirm_uc, last_pv_id=uuid4(),
    )
    assert repo.plan_versions[pv.id].status == "committed"


@pytest.mark.asyncio
async def test_handle_text_propose_via_task_then_typed_ok_commits():
    """Bug: typed «ок» after a normal /task proposal must commit.

    The normal path persists the proposed pv_id into FSM context; a later
    ConfirmIntent (no explicit id, no last_pv_id) reads pending_pv_id from FSM
    and commits — not just inside the edit loop.
    """
    from datetime import timedelta

    from planner.app.add_project import ProjectTemplate, TemplateTaskSpec
    from planner.app.confirm_plan import ConfirmPlanUseCase
    from planner.app.ports import PersonRecord
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
    solver = GreedySolver(WeekendCalendar())
    confirm_uc = ConfirmPlanUseCase(repo)  # type: ignore[arg-type]
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)

    # FSM state backed by a dict so the proposal's pv_id survives to the «ок».
    fsm: dict[str, Any] = {}

    class _State:
        async def get_data(self) -> dict[str, Any]:
            return dict(fsm)

        async def update_data(self, **kw: Any) -> None:
            fsm.update(kw)

        async def set_state(self, _state: Any) -> None:
            pass

        async def clear(self) -> None:
            fsm.clear()

    state = _State()

    # 1) Propose a plan via the normal /task path.
    add_intent = AddProjectIntent(
        title="Тест", template_code="standard",
        deadline=date.today() + timedelta(days=30),
    )
    msg, _ = _message()
    pv_id = await _handle_text(
        msg, "Тест", _FakeParser(add_intent), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=solver, actor_record=actor_record,
        confirm_uc=confirm_uc, edit_state=state,
    )
    assert pv_id is not None
    assert fsm.get("pending_pv_id") == str(pv_id)  # persisted for typed «ок»

    # 2) Type «ок» — no explicit id, no last_pv_id; must read FSM and commit.
    msg2, answers2 = _message()
    await _handle_text(
        msg2, "ок", _FakeParser(ConfirmIntent()), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, actor_record=actor_record,
        confirm_uc=confirm_uc, edit_state=state,
    )
    assert repo.plan_versions[pv_id].status == "committed"
    assert "зафиксирован" in answers2.calls[0].lower()


@pytest.mark.asyncio
async def test_handle_text_confirm_no_plan_replies_friendly():
    """No explicit id and no context proposal → friendly 'нет плана' message."""
    from planner.app.confirm_plan import ConfirmPlanUseCase
    from planner.app.ports import PersonRecord

    repo = _ConfirmRepo()
    confirm_uc = ConfirmPlanUseCase(repo)  # type: ignore[arg-type]
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)

    msg, answers = _message()
    await _handle_text(
        msg, "ок", _FakeParser(ConfirmIntent()), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, actor_record=actor_record,  # type: ignore[arg-type]
        confirm_uc=confirm_uc, last_pv_id=None,
    )
    assert "нет плана" in answers.calls[0].lower()


@pytest.mark.asyncio
async def test_handle_text_confirm_not_proposed_replies_error():
    """A stale / already-committed plan → 'не найден или уже зафиксирован'."""
    from planner.app.confirm_plan import ConfirmPlanUseCase
    from planner.app.ports import PersonRecord, PlanVersionRecord

    repo = _ConfirmRepo()
    pv = PlanVersionRecord(uuid4(), uuid4(), "committed", {})
    repo.plan_versions[pv.id] = pv
    confirm_uc = ConfirmPlanUseCase(repo)  # type: ignore[arg-type]
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)

    msg, answers = _message()
    await _handle_text(
        msg, "ок", _FakeParser(ConfirmIntent()), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, actor_record=actor_record,  # type: ignore[arg-type]
        confirm_uc=confirm_uc, last_pv_id=pv.id,
    )
    reply = answers.calls[0].lower()
    assert "не найден" in reply or "зафиксирован" in reply


@pytest.mark.asyncio
async def test_handle_edit_text_confirm_clears_state():
    """M7/L4: typing «ок» inside the edit loop commits and CLEARS the state."""
    from planner.app.confirm_plan import ConfirmPlanUseCase
    from planner.app.ports import PersonRecord, PlanVersionRecord
    from planner.bot.handlers.task_router import handle_edit_text

    repo = _ConfirmRepo()
    pv = PlanVersionRecord(uuid4(), uuid4(), "proposed", {})
    repo.plan_versions[pv.id] = pv
    confirm_uc = ConfirmPlanUseCase(repo)  # type: ignore[arg-type]
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)

    msg, answers = _message("ок")
    state = SimpleNamespace(
        clear=AsyncMock(),
        set_state=AsyncMock(),
        update_data=AsyncMock(),
        get_data=AsyncMock(return_value={"pending_pv_id": str(pv.id)}),
    )
    await handle_edit_text(
        msg, state, _FakeParser(ConfirmIntent()), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, actor_record=actor_record, confirm_uc=confirm_uc,  # type: ignore[arg-type]
    )
    assert repo.plan_versions[pv.id].status == "committed"
    assert state.clear.called
    assert not state.set_state.called  # confirm does NOT re-arm


@pytest.mark.asyncio
async def test_handle_text_load_intent_renders_photo():
    """NL load query must render the heatmap photo, not just echo a text line."""
    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import _handle_text
    from planner.domain.intent import LoadIntent

    repo = SimpleNamespace(
        list_people=AsyncMock(return_value=[SimpleNamespace(id=uuid4(), name="Андрей")]),
        list_projects=AsyncMock(return_value=[]),
        get_solver_people=AsyncMock(
            return_value=[SimpleNamespace(id=uuid4(), name="Андрей", capacity_h=8)]
        ),
        list_committed_plans=AsyncMock(return_value=[]),
    )
    msg = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock())
    actor_record = PersonRecord(id=uuid4(), name="Андрей", is_admin=True)
    await _handle_text(
        msg,  # type: ignore[arg-type]
        "какая загрузка у команды?",
        _FakeParser(LoadIntent(person_name=None)),
        {"is_admin": True},
        repo=repo,  # type: ignore[arg-type]
        actor_record=actor_record,
    )
    assert msg.answer_photo.called, "load query must render a photo, not echo text"
    assert not msg.answer.called


@pytest.mark.asyncio
async def test_handle_text_compound_runs_load_and_capture():
    """A compound message ("какая загрузка у Андрея? Если свободно, поставь
    задачу") yields TWO intents — the handler must execute BOTH: render the
    load heatmap AND capture the task (spec multi-intent)."""
    from planner.app.ports import PersonRecord

    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo = _FakeRepo(people=(andrey,), plans=[])
    msg, answers = _message()
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    intents = [
        LoadIntent(person_name="Андрей"),
        CaptureTaskIntent(task_title="добрифовать МТС", assignee_names=["Андрей"]),
    ]
    await _handle_text(
        msg,  # type: ignore[arg-type]
        "какая загрузка у Андрея? Если свободно, поставь задачу добрифовать МТС",
        _MultiParser(intents),  # type: ignore[arg-type]
        {"is_admin": True},
        repo=repo,  # type: ignore[arg-type]
        actor_record=actor_record,
    )
    # Load ran → a heatmap photo was sent.
    assert answers.photos, "load intent must render a photo"
    # Capture ran → the task was written and confirmed.
    assert repo.captured_tasks == ["добрифовать МТС"]
    assert any("Записал" in c for c in answers.calls), "capture must confirm"


# ---------------------------------------------------------------------------
# Agent path (Task 3) — when an agent dep is present and repo is set, the
# tool-use agent runs; otherwise the legacy parse_intents path runs.
# ---------------------------------------------------------------------------

class _FakeAgent:
    """Records run() args and returns a canned AgentReply."""

    def __init__(self, reply: Any) -> None:
        self._reply = reply
        self.calls: list[tuple[str, Any, Any]] = []

    async def run(self, text: str, ctx: Any, toolbox: Any) -> Any:
        self.calls.append((text, ctx, toolbox))
        return self._reply


class _ExplodingParser:
    """Parser double whose parse_intents must never be called on the agent path."""

    async def parse(self, text: str, ctx: Any) -> Any:  # pragma: no cover
        raise AssertionError("legacy parse() must not run when agent is active")

    async def parse_intents(self, text: str, ctx: Any) -> list[Any]:
        raise AssertionError("legacy parse_intents() must not run when agent is active")


@pytest.mark.asyncio
async def test_handle_text_agent_path_answers_without_legacy_parser():
    """Agent present + repo set → agent.run drives the reply; parser untouched."""
    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import _handle_text
    from planner.infra.llm.agent import AgentReply

    repo = _FakeRepo(people=(), plans=[])
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    agent = _FakeAgent(AgentReply(text="Готово, записал задачу."))

    captured: list[Any] = []

    async def _answer(text: str, reply_markup: Any = None, **kw: Any) -> None:
        captured.append((text, reply_markup))

    msg = SimpleNamespace(
        text="поставь задачу", answer=_answer,
        chat=SimpleNamespace(type="private", id=7),
        reply_to_message=None, bot=None,
    )
    pv = await _handle_text(
        msg, "поставь задачу", _ExplodingParser(),  # type: ignore[arg-type]
        {"is_admin": True}, repo=repo, solver=GreedySolver(WeekendCalendar()),
        actor_record=actor_record, agent=agent,  # type: ignore[arg-type]
    )
    assert agent.calls, "agent.run must be invoked"
    assert captured == [("Готово, записал задачу.", None)]
    assert pv is None  # no proposed plan → no pv returned


@pytest.mark.asyncio
async def test_handle_text_agent_path_attaches_keyboard_on_proposed_plan():
    """A proposed_pv_id on the AgentReply → ✅/✏️ buttons + pv returned."""
    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import _handle_text
    from planner.infra.llm.agent import AgentReply

    repo = _FakeRepo(people=(), plans=[])
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    pv_id = uuid4()
    agent = _FakeAgent(AgentReply(text="Предложил план.", proposed_pv_id=pv_id))

    captured: list[Any] = []

    async def _answer(text: str, reply_markup: Any = None, **kw: Any) -> None:
        captured.append((text, reply_markup))

    msg = SimpleNamespace(
        text="спланируй проект", answer=_answer,
        chat=SimpleNamespace(type="private", id=8),
        reply_to_message=None, bot=None,
    )
    pv = await _handle_text(
        msg, "спланируй проект", _ExplodingParser(),  # type: ignore[arg-type]
        {"is_admin": True}, repo=repo, solver=GreedySolver(WeekendCalendar()),
        actor_record=actor_record, agent=agent,  # type: ignore[arg-type]
    )
    assert pv == pv_id
    (text, kb), = captured
    assert text == "Предложил план."
    assert kb is not None, "proposed plan must carry the confirm keyboard"


@pytest.mark.asyncio
async def test_handle_text_no_agent_uses_legacy_parser():
    """agent is None → existing parse_intents/dispatch path runs unchanged."""
    from planner.bot.handlers.task_router import _handle_text

    intent = ClarifyIntent(question="Уточни задачу.")
    msg, answers = _message()
    await _handle_text(
        msg, "что угодно", _FakeParser(intent), {"is_admin": True}, agent=None  # type: ignore[arg-type]
    )
    assert answers.calls == ["Уточни задачу."]


@pytest.mark.asyncio
async def test_handle_text_agent_skipped_when_repo_none():
    """Agent present but repo is None (degraded/echo) → legacy parse path runs."""
    from planner.bot.handlers.task_router import _handle_text
    from planner.infra.llm.agent import AgentReply

    intent = ClarifyIntent(question="Нет базы.")
    agent = _FakeAgent(AgentReply(text="не должно вызваться"))
    msg, answers = _message()
    await _handle_text(
        msg, "что угодно", _FakeParser(intent), {"is_admin": True},
        repo=None, agent=agent,  # type: ignore[arg-type]
    )
    assert not agent.calls, "agent must not run without a repo"
    assert answers.calls == ["Нет базы."]


@pytest.mark.asyncio
async def test_handle_task_threads_agent_to_handle_text():
    """/task <text> with an agent dep routes through the agent path."""
    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import handle_task
    from planner.infra.llm.agent import AgentReply

    repo = _FakeRepo(people=(), plans=[])
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    agent = _FakeAgent(AgentReply(text="agent-handled"))
    msg, answers = _message("/task сделай магию")
    await handle_task(
        msg, _ExplodingParser(), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=GreedySolver(WeekendCalendar()),
        actor_record=actor_record, agent=agent,  # type: ignore[arg-type]
    )
    assert agent.calls and agent.calls[0][0] == "сделай магию"
    assert answers.calls == ["agent-handled"]


@pytest.mark.asyncio
async def test_handle_mention_threads_agent_to_handle_text():
    """A DM with an agent dep routes through the agent path."""
    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import handle_mention_or_dm
    from planner.infra.llm.agent import AgentReply

    repo = _FakeRepo(people=(), plans=[])
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    agent = _FakeAgent(AgentReply(text="dm-handled"))
    msg, answers = _message("привет, спланируй", chat_type="private")
    await handle_mention_or_dm(
        msg, _ExplodingParser(), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=GreedySolver(WeekendCalendar()),
        actor_record=actor_record, agent=agent,  # type: ignore[arg-type]
    )
    assert agent.calls
    assert answers.calls == ["dm-handled"]


@pytest.mark.asyncio
async def test_handle_voice_threads_agent_to_handle_text():
    """A voice message with an agent dep routes the transcribed text to the agent."""
    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import handle_voice
    from planner.infra.llm.agent import AgentReply

    repo = _FakeRepo(people=(), plans=[])
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    agent = _FakeAgent(AgentReply(text="voice-handled"))
    msg, answers = _message()
    audio_buf = SimpleNamespace(read=lambda: b"audio")
    file_obj = SimpleNamespace(file_path="voice/f.ogg")
    msg.voice = SimpleNamespace(file_id="abc", file_size=1000)
    msg.bot = SimpleNamespace(
        get_file=AsyncMock(return_value=file_obj),
        download_file=AsyncMock(return_value=audio_buf),
    )
    stt = SimpleNamespace(transcribe=AsyncMock(return_value="спланируй проект"))
    await handle_voice(
        msg, _ExplodingParser(), {"is_admin": True}, stt=stt,  # type: ignore[arg-type]
        repo=repo, solver=GreedySolver(WeekendCalendar()),
        actor_record=actor_record, agent=agent,  # type: ignore[arg-type]
    )
    assert agent.calls and agent.calls[0][0] == "спланируй проект"
    assert "voice-handled" in answers.calls


# ---------------------------------------------------------------------------
# Legacy dispatch must thread project_sink (strict rule: a created project
# ALWAYS carries its Notion master-card link)
# ---------------------------------------------------------------------------

class _RecordingProjectSink:
    """ProjectSinkPort double: records create_card calls, returns a page id."""

    def __init__(self) -> None:
        self.created: list[Any] = []

    async def create_card(self, project: Any) -> str:
        self.created.append(project)
        return "page-123"

    async def mark_task_done(self, page_id: str, task_name: str) -> bool:
        return True


def _add_project_repo() -> Any:
    from planner.app.add_project import ProjectTemplate, TemplateTaskSpec
    from tests.unit.app.conftest import FakeRepo

    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo = FakeRepo()
    repo.solver_people = (andrey,)
    repo.templates = {
        "standard": ProjectTemplate(
            code="standard", tasks=(TemplateTaskSpec(1, "Бриф", 8, (andrey.id,)),)
        )
    }
    return repo


@pytest.mark.asyncio
async def test_handle_text_legacy_add_project_creates_notion_card_with_link():
    """The legacy (no-agent) dispatch must pass project_sink through so the
    Notion master card is created and its link lands in the reply."""
    from datetime import timedelta

    from planner.app.ports import PersonRecord

    repo = _add_project_repo()
    sink = _RecordingProjectSink()
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    intent = AddProjectIntent(
        title="Тест", template_code="standard",
        deadline=date.today() + timedelta(days=30),
    )
    msg, answers = _message()
    await _handle_text(
        msg, "Тест", _FakeParser(intent), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=GreedySolver(WeekendCalendar()),
        actor_record=actor_record, project_sink=sink,  # type: ignore[arg-type]
    )
    assert len(sink.created) == 1, "project sink must receive the master card"
    assert any("notion.so" in c for c in answers.calls), "reply must carry the link"


@pytest.mark.asyncio
async def test_handle_edit_text_add_project_creates_notion_card_with_link():
    """The edit-loop re-dispatch must also carry project_sink → Notion link."""
    from datetime import timedelta

    from planner.app.ports import PersonRecord
    from planner.bot.handlers.task_router import handle_edit_text

    repo = _add_project_repo()
    sink = _RecordingProjectSink()
    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    intent = AddProjectIntent(
        title="Правка", template_code="standard",
        deadline=date.today() + timedelta(days=30),
    )
    msg, answers = _message("правка: новый план")
    state = SimpleNamespace(
        clear=AsyncMock(),
        set_state=AsyncMock(),
        update_data=AsyncMock(),
        get_data=AsyncMock(return_value={}),
    )
    await handle_edit_text(
        msg, state, _FakeParser(intent), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=GreedySolver(WeekendCalendar()),
        actor_record=actor_record, project_sink=sink,  # type: ignore[arg-type]
    )
    assert len(sink.created) == 1, "project sink must receive the master card"
    assert any("notion.so" in c for c in answers.calls), "reply must carry the link"
