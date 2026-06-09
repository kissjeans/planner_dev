"""/task and @mention router (spec section 8.1).

Parses the message into an intent, enforces the write-gate, and — when the
solver/repo are wired — runs the AddProject flow and replies with the explained
plan. Without those deps it degrades to a human-readable interpretation.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from planner.app.add_project import (
    AddProjectUseCase,
    InvalidProjectError,
    deserialize_allocations,
)
from planner.app.explain_plan import ExplainPlanUseCase
from planner.app.ports import PersonRecord, RepoPort
from planner.bot.states import PlanEditState
from planner.domain.intent import (
    AddProjectIntent,
    AssignIntent,
    ClarifyIntent,
    ConfirmIntent,
    Intent,
    LoadIntent,
    VacationIntent,
    WhatIfIntent,
)
from planner.domain.models import DayAllocation
from planner.domain.permissions import can_execute
from planner.domain.solver.ports import SolverPort
from planner.infra.llm.ports import ChatContext, IntentParserPort
from planner.infra.stt.whisper import STTPort

router = Router(name="task")


def _plan_keyboard(pv_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{pv_id}"),
        InlineKeyboardButton(text="✏️ Правка", callback_data=f"edit:{pv_id}"),
    ]])


async def build_add_project_reply(
    intent: AddProjectIntent,
    *,
    repo: RepoPort,
    solver: SolverPort,
    actor_record: PersonRecord,
    today: date,
    explain_uc: ExplainPlanUseCase | None = None,
) -> tuple[str, UUID | None]:
    """Run AddProject, return (text, plan_version_id). pv_id is None on error."""
    template = await repo.get_project_template(intent.template_code)
    if template is None:
        return f"Шаблон «{intent.template_code}» не найден.", None

    people = await repo.get_solver_people()
    if not people:
        return "В команде нет активных людей — некому планировать.", None

    existing: list[DayAllocation] = []
    for payload in await repo.list_committed_plans():
        existing.extend(deserialize_allocations(payload))

    uc = AddProjectUseCase(repo, solver)
    try:
        result = await uc.execute(
            intent,
            actor_record,
            tuple(people),
            template,
            today=today,
            existing_allocations=tuple(existing),
        )
    except InvalidProjectError as exc:
        return f"Не могу создать проект: {exc}", None

    task_names = {t.id: t.name for t in result.tasks}
    person_names = {p.id: p.name for p in people}
    explainer = explain_uc or ExplainPlanUseCase(None)
    summary = await explainer.execute(
        result.plan,
        task_names,
        person_names,
        deadline=intent.deadline,
        earliest_end=result.earliest_end,
    )
    text = f"Проект «{result.project.title}» — предложенный план:\n{summary}"
    return text, result.plan_version_id


def describe_intent(intent: Intent) -> str:
    """Render a parsed intent as a short Russian confirmation line."""
    if isinstance(intent, AddProjectIntent):
        when = intent.deadline.isoformat() if intent.deadline else "обратный режим (КП)"
        return f"Проект «{intent.title}», шаблон {intent.template_code}, дедлайн: {when}."
    if isinstance(intent, LoadIntent):
        who = intent.person_name or "вся команда"
        return f"Загрузка: {who}."
    if isinstance(intent, WhatIfIntent):
        return f"Что-если: {intent.operation}, проект {intent.project_title or '—'}."
    if isinstance(intent, VacationIntent):
        return f"Отпуск {intent.person_name}: {intent.day_from}–{intent.day_to}."
    if isinstance(intent, ConfirmIntent):
        return "Подтверждение последнего предложенного плана."
    if isinstance(intent, AssignIntent):
        return f"Назначить {intent.task_ref} на {intent.person_name}."
    return intent.question or "Не понял команду."


async def _handle_text(
    message: Message,
    text: str,
    parser: IntentParserPort,
    actor: dict[str, Any],
    *,
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
    actor_record: PersonRecord | None = None,
    explain_uc: ExplainPlanUseCase | None = None,
) -> None:
    ctx = ChatContext(today=date.today())
    intent = await parser.parse(text, ctx)

    if isinstance(intent, ClarifyIntent):
        await message.answer(describe_intent(intent))
        return

    if not can_execute(intent.kind, actor.get("is_admin", False)):
        await message.answer("Только админ может править план.")
        return

    if (
        isinstance(intent, AddProjectIntent)
        and repo is not None
        and solver is not None
        and actor_record is not None
    ):
        text, pv_id = await build_add_project_reply(
            intent,
            repo=repo,
            solver=solver,
            actor_record=actor_record,
            today=date.today(),
            explain_uc=explain_uc,
        )
        kb = _plan_keyboard(pv_id) if pv_id is not None else None
        await message.answer(text, reply_markup=kb)
        return

    await message.answer(describe_intent(intent))


@router.message(F.voice)
async def handle_voice(
    message: Message,
    parser: IntentParserPort,
    actor: dict[str, Any],
    stt: STTPort | None = None,
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
    actor_record: PersonRecord | None = None,
    explain_uc: ExplainPlanUseCase | None = None,
) -> None:
    if stt is None or message.voice is None or message.bot is None:
        await message.answer("Голосовые сообщения не поддерживаются — напиши текстом.")
        return
    bot = message.bot
    file = await bot.get_file(message.voice.file_id)
    assert file.file_path is not None
    audio = await bot.download_file(file.file_path)
    assert audio is not None
    text = await stt.transcribe(audio.read(), "voice.ogg")
    if not text:
        await message.answer("Не удалось распознать голос — напиши текстом.")
        return
    await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
    )


@router.message(Command("task"))
async def handle_task(
    message: Message,
    parser: IntentParserPort,
    actor: dict[str, Any],
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
    actor_record: PersonRecord | None = None,
    explain_uc: ExplainPlanUseCase | None = None,
) -> None:
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Напиши, что нужно: /task <текст>.")
        return
    await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
    )


@router.message(StateFilter(PlanEditState.waiting), F.text)
async def handle_edit_text(
    message: Message,
    state: FSMContext,
    parser: IntentParserPort,
    actor: dict[str, Any],
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
    actor_record: PersonRecord | None = None,
    explain_uc: ExplainPlanUseCase | None = None,
) -> None:
    """FSM edit loop (spec flow step 14 / scenario J).

    Receives the manager's free-text edit instruction after they clicked
    "правка" on a proposed plan. Re-runs the intent parser and proposes a
    fresh plan. Stays in the edit state until the manager types «ок» (which
    resolves to ConfirmIntent and is handled by the normal flow, then clears
    state).
    """
    text = (message.text or "").strip()
    if not text:
        return
    await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
    )
    # Clear edit state so subsequent messages go through the normal handler.
    await state.clear()


@router.message(F.text & ~F.text.startswith("/"))
async def handle_mention_or_dm(
    message: Message,
    parser: IntentParserPort,
    actor: dict[str, Any],
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
    actor_record: PersonRecord | None = None,
    explain_uc: ExplainPlanUseCase | None = None,
) -> None:
    """Handle @mention in groups and direct messages in private chats (spec 8.1).

    In groups, only react when the bot is directly @mentioned or the message
    is a reply to the bot. In private chats, always respond.
    """
    raw = message.text or ""

    if message.chat.type != "private" and message.bot is not None:
        # Group / supergroup: only respond when bot is @mentioned or replied-to.
        bot_info = await message.bot.get_me()
        bot_mention = f"@{bot_info.username}".lower()
        is_reply_to_bot = (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and message.reply_to_message.from_user.id == bot_info.id
        )
        if bot_mention not in raw.lower() and not is_reply_to_bot:
            return

    # Strip leading @botname if present so parser gets clean text.
    text = raw.partition(" ")[2].strip() if raw.lower().startswith("@") else raw.strip()
    if not text:
        return
    await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
    )
