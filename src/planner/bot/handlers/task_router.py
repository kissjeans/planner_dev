"""/task and @mention router (spec section 8.1).

Parses the message into an intent, enforces the write-gate, and — when the
solver/repo are wired — runs the AddProject flow and replies with the explained
plan. Without those deps it degrades to a human-readable interpretation.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from planner.infra.llm.agent import PlannerAgent

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from planner.app.add_project import (
    AddProjectUseCase,
    InvalidProjectError,
    deserialize_allocations,
)
from planner.app.capture_task import CaptureResult, CaptureTaskUseCase
from planner.app.confirm_plan import (
    ConfirmPlanUseCase,
    PlanNotFoundError,
    PlanNotProposedError,
)
from planner.app.explain_plan import ExplainPlanUseCase
from planner.app.load_summary import DEFAULT_DAYS
from planner.app.ports import PersonRecord, RepoPort, TaskMeta, TaskSinkPort
from planner.app.suggest_assignees import SuggestAssigneesUseCase
from planner.bot.states import PlanEditState
from planner.domain.intent import (
    AddProjectIntent,
    AssignIntent,
    CaptureTaskIntent,
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
from planner.infra.history import ChatHistory
from planner.infra.llm.ports import ChatContext, IntentParserPort
from planner.infra.stt.ports import STTPort

router = Router(name="task")
_MAX_VOICE_BYTES = 20 * 1024 * 1024  # 20 MB cap on voice downloads
_STT_TIMEOUT_S = 60  # past this, ask the user to retry — never hang silently


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


def _match_tasks(task_ref: str, tasks: list[TaskMeta]) -> list[TaskMeta]:
    """Best-effort resolve a free-text task reference to candidate tasks.

    Prefers an exact (case-insensitive) name match; otherwise falls back to
    tasks whose name appears inside the reference (handles refs like
    «Дизайн обложки в проекте Альфа»). Returns all candidates so the caller can
    detect ambiguity instead of guessing.
    """
    ref = task_ref.strip().casefold()
    if not ref:
        return []
    exact = [t for t in tasks if t.task_name.casefold() == ref]
    if exact:
        return exact
    return [t for t in tasks if t.task_name.casefold() in ref]


async def build_assign_reply(
    intent: AssignIntent, *, repo: RepoPort, actor_id: UUID | None
) -> str:
    """Resolve person + task and reassign, or return a clarifying question.

    Reuses repo.set_task_assignee (persisted assignment) and reassign_in_plan
    (committed-plan payload) — the same pair the web board uses (board.py:78).
    Never guesses on an ambiguous/unknown task; asks the manager to clarify.
    """
    person = await repo.get_person_by_name(intent.person_name)
    if person is None:
        return f"Не нашёл человека «{intent.person_name}» — уточни имя."

    candidates = _match_tasks(intent.task_ref, await repo.list_tasks_with_meta())
    if not candidates:
        return f"Не нашёл задачу «{intent.task_ref}» — это не наша задача? Уточни название."
    if len(candidates) > 1:
        names = ", ".join(sorted({t.task_name for t in candidates}))
        return f"Нашёл несколько задач ({names}) — уточни, какую назначить."

    task = candidates[0]
    moved = await repo.set_task_assignee(task.task_id, person.id)
    if not moved:
        return f"Не нашёл задачу «{intent.task_ref}» — уточни название."
    await repo.reassign_in_plan(task.task_id, person.id)
    await repo.add_audit(
        actor_id,
        "reassign_task",
        "task",
        task.task_id,
        {"person_id": str(person.id)},
    )
    return f"Назначил «{task.task_name}» на {person.name}."


_MAX_SUGGESTED = 3


async def build_capture_reply(
    intent: CaptureTaskIntent,
    *,
    repo: RepoPort,
    actor_record: PersonRecord | None,
    task_sink: TaskSinkPort | None = None,
) -> tuple[str, CaptureResult]:
    """Capture the task into the DB; return the confirmation line and the result.

    When nobody is named but the LLM inferred required skills, append a
    *suggestion* of who could take it (spec section 5). This never auto-assigns
    — a named assignee leaves the flow unchanged. The :class:`CaptureResult` is
    returned alongside so the agent path can surface ``notion_url`` itself (the
    model drops links when it paraphrases tool output).
    """
    result = await CaptureTaskUseCase(repo, sink=task_sink).execute(intent, actor_record)
    hint = await _suggestion_hint(intent, repo=repo)
    text = format_capture_confirmation(
        title=result.task_title,
        project=result.project_title,
        assignees=result.assignee_names,
        deadline_iso=result.deadline_iso,
        hint=hint,
    )
    if result.notion_url:
        text += f"\n\n🔗 Notion: {result.notion_url}"
    return text, result


def format_capture_confirmation(
    *,
    title: str,
    project: str,
    assignees: list[str],
    deadline_iso: str | None,
    hint: str | None = None,
) -> str:
    """Deterministic capture confirmation (the clean fixed-field layout — no
    markdown tables, which Telegram doesn't render). The bot shows this verbatim
    instead of the model's free-form narration so several assignees on one task
    read as one task, formatted consistently."""
    lines = [
        "✓ Записал",
        f"  задача: {title}",
        f"  проект: {project}",
        f"  кому: {', '.join(assignees) or '—'}",
        f"  дедлайн: {deadline_iso or '—'}",
    ]
    if hint is not None:
        lines.append(hint)
    return "\n".join(lines)


def _append_notion_links(text: str, urls: tuple[str, ...]) -> str:
    """Append captured Notion links the model omitted when paraphrasing tools."""
    extra = [u for u in urls if u and u not in text]
    if not extra:
        return text
    links = "\n".join(f"🔗 Notion: {u}" for u in extra)
    return f"{text}\n\n{links}"


async def _suggestion_hint(
    intent: CaptureTaskIntent, *, repo: RepoPort
) -> str | None:
    """Build a «Предлагаю: …» line, or None when there is nothing to suggest."""
    if intent.assignee_names or not intent.required_skills:
        return None
    suggestions = await SuggestAssigneesUseCase(repo).execute(intent.required_skills)
    names = [s.name for s in suggestions if s.coverage > 0][:_MAX_SUGGESTED]
    if not names:
        return None
    return f"  предлагаю: {', '.join(names)}"


def describe_intent(intent: Intent) -> str:
    """Render a parsed intent as a short Russian confirmation line."""
    if isinstance(intent, AddProjectIntent):
        when = intent.deadline.isoformat() if intent.deadline else "обратный режим (КП)"
        return f"Проект «{intent.title}», шаблон {intent.template_code}, дедлайн: {when}."
    if isinstance(intent, CaptureTaskIntent):
        who = ", ".join(intent.assignee_names) or "не назначено"
        return f"Задача: {intent.task_title} (кому: {who})."
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


async def _confirm_latest(
    message: Message,
    intent: ConfirmIntent,
    *,
    confirm_uc: ConfirmPlanUseCase | None,
    actor_record: PersonRecord | None,
    last_pv_id: UUID | None,
) -> bool:
    """Commit the proposed plan a typed «ок» refers to (L4).

    Mirrors the inline ✅ button (bot/handlers/confirm.py). The target is the
    intent's explicit id, else the latest proposed plan carried in context.
    Returns True when a commit was attempted (so callers can clear FSM state).
    """
    target = intent.plan_version_id or last_pv_id
    if target is None:
        await message.answer("Нет плана на подтверждение — сначала предложи план.")
        return False
    if confirm_uc is None or actor_record is None:
        await message.answer("База данных не подключена.")
        return False
    try:
        await confirm_uc.execute(target, actor_record)
        await message.answer("План зафиксирован.")
        return True
    except (PlanNotFoundError, PlanNotProposedError):
        await message.answer("План не найден или уже зафиксирован.")
        return False


async def _dispatch_intent(
    message: Message,
    intent: Intent,
    actor: dict[str, Any],
    *,
    repo: RepoPort | None,
    solver: SolverPort | None,
    actor_record: PersonRecord | None,
    explain_uc: ExplainPlanUseCase | None,
    confirm_uc: ConfirmPlanUseCase | None,
    last_pv_id: UUID | None,
    edit_state: FSMContext | None,
    task_sink: TaskSinkPort | None,
) -> UUID | None:
    """Execute a single parsed intent (the isinstance dispatch chain).

    Returns the proposed plan-version id for an AddProject proposal (so the
    caller can persist it for a later typed «ок»), else None.
    """
    if isinstance(intent, ClarifyIntent):
        await message.answer(describe_intent(intent))
        return None

    if not can_execute(intent.kind, actor.get("is_admin", False)):
        await message.answer("Только админ может править план.")
        return None

    if isinstance(intent, ConfirmIntent):
        if confirm_uc is None and repo is None:
            await message.answer(describe_intent(intent))
            return None
        # A typed «ок» after a normal /task proposal (not the edit loop) resolves
        # the pending plan from FSM context, not just from the passed last_pv_id.
        target_pv_id = last_pv_id
        if target_pv_id is None and edit_state is not None:
            data = await edit_state.get_data()
            target_pv_id = _parse_pv_id(data.get("pending_pv_id"))
        committed = await _confirm_latest(
            message, intent,
            confirm_uc=confirm_uc, actor_record=actor_record, last_pv_id=target_pv_id,
        )
        # A typed «ок» ends the edit loop (spec flow step 14): clear FSM state.
        if committed and edit_state is not None:
            await edit_state.clear()
        return None

    if isinstance(intent, CaptureTaskIntent):
        if repo is None:
            await message.answer(describe_intent(intent))
            return None
        text, _ = await build_capture_reply(
            intent, repo=repo, actor_record=actor_record, task_sink=task_sink
        )
        await message.answer(text)
        return None

    if (
        isinstance(intent, AssignIntent)
        and repo is not None
        and actor_record is not None
    ):
        await message.answer(
            await build_assign_reply(
                intent, repo=repo, actor_id=actor_record.id
            )
        )
        return None

    if (
        isinstance(intent, AddProjectIntent)
        and repo is not None
        and solver is not None
        and actor_record is not None
    ):
        reply_text, pv_id = await build_add_project_reply(
            intent,
            repo=repo,
            solver=solver,
            actor_record=actor_record,
            today=date.today(),
            explain_uc=explain_uc,
        )
        kb = _plan_keyboard(pv_id) if pv_id is not None else None
        await message.answer(reply_text, reply_markup=kb)
        # Persist the proposal so a later typed «ок» can confirm it even outside
        # the edit loop (the inline ✅ button carries the id in its callback).
        if pv_id is not None and edit_state is not None:
            await edit_state.update_data(pending_pv_id=str(pv_id))
        return pv_id

    if isinstance(intent, LoadIntent) and repo is not None:
        from planner.bot.handlers.load import build_load_image

        png = await build_load_image(
            repo, start=date.today(), person_name=intent.person_name
        )
        who = intent.person_name or "вся команда"
        if png is None:
            await message.answer("В команде нет активных людей — нечего показывать.")
            return None
        await message.answer_photo(
            BufferedInputFile(png, filename="load.png"),
            caption=f"Загрузка ({who}) на {DEFAULT_DAYS} дней.",
        )
        return None

    await message.answer(describe_intent(intent))
    return None


def _intents_to_dispatch(intents: list[Intent]) -> list[Intent]:
    """Pick which intents to execute for a (possibly compound) message.

    A compound message may carry a ClarifyIntent alongside real actions when the
    parser is unsure about part of it. Prefer the real actions: skip a lone
    clarify when other intents exist. If ALL are clarify, answer the (first)
    clarify so the user still gets a prompt.
    """
    real: list[Intent] = [i for i in intents if not isinstance(i, ClarifyIntent)]
    if real:
        return real
    return intents[:1]


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
    confirm_uc: ConfirmPlanUseCase | None = None,
    last_pv_id: UUID | None = None,
    edit_state: FSMContext | None = None,
    task_sink: TaskSinkPort | None = None,
    history: ChatHistory | None = None,
    agent: PlannerAgent | None = None,
) -> UUID | None:
    # Known-sender gate (spec 16 + QA H1/H2): only resolved team members or
    # admins may have their messages parsed/acted on. This blocks strangers
    # from writing to the DB and from spending the LLM budget. When repo is
    # None we are in degraded/echo mode (no DB) — skip the gate so the bot can
    # still interpret messages offline.
    if repo is not None and actor_record is None and not actor.get("is_admin", False):
        await message.answer(
            "Не узнал тебя — я отвечаю только участникам команды. "
            "Попроси администратора добавить тебя."
        )
        return None
    known_people: tuple[str, ...] = ()
    known_projects: tuple[str, ...] = ()
    if repo is not None:
        known_people = tuple(p.name for p in await repo.list_people())
        known_projects = tuple(pr.title for pr in await repo.list_projects())
    # Short-term per-chat history lets the parser resolve follow-up references
    # («тогда ставь на Андрея», «на него»). Capture recent BEFORE recording the
    # current message so it is not part of its own context.
    recent: tuple[str, ...] = ()
    if history is not None and message.chat is not None:
        recent = history.recent(message.chat.id)
        history.record(message.chat.id, text)
    ctx = ChatContext(
        today=date.today(),
        known_people=known_people,
        known_projects=known_projects,
        recent_messages=recent,
    )
    # Tool-use agent path (Task 3): when an agent is wired and the DB is
    # available, the agent reads/reasons/acts via tools instead of the rigid
    # enum classifier. The ToolBox is request-scoped (carries this request's
    # actor), so it is built here per message. Falls through to the legacy
    # parse_intents path below when there is no agent or no repo (echo mode).
    if agent is not None and repo is not None and solver is not None:
        from planner.infra.llm.tools import ToolBox

        toolbox = ToolBox(
            repo=repo,
            solver=solver,
            actor=actor,
            actor_record=actor_record,
            task_sink=task_sink,
        )
        reply = await agent.run(text, ctx, toolbox)
        # Missing a key field → drive deterministic button clarify, not free text.
        if reply.clarify is not None and edit_state is not None:
            from planner.bot.handlers.clarify import start_capture_clarify

            await start_capture_clarify(message, edit_state, reply.clarify, repo)
            return None
        # Captures → show the deterministic confirmations verbatim (clean layout,
        # one-task merges), not the model's free narration. Otherwise its text.
        base = (
            "\n\n".join(reply.captured_replies)
            if reply.captured_replies else reply.text
        )
        final_text = _append_notion_links(base, reply.notion_urls)
        kb = _plan_keyboard(reply.proposed_pv_id) if reply.proposed_pv_id else None
        await message.answer(final_text, reply_markup=kb)
        if history is not None and message.chat is not None:
            history.record(message.chat.id, final_text)
        return reply.proposed_pv_id
    # A compound message ("какая загрузка у Андрея? Если свободно, поставь
    # задачу") carries several actions — dispatch EACH in order. Single-action
    # messages yield a one-element list, so the loop runs once unchanged.
    intents = await parser.parse_intents(text, ctx)
    last_pv: UUID | None = None
    for intent in _intents_to_dispatch(intents):
        pv_id = await _dispatch_intent(
            message, intent, actor,
            repo=repo, solver=solver, actor_record=actor_record,
            explain_uc=explain_uc, confirm_uc=confirm_uc, last_pv_id=last_pv_id,
            edit_state=edit_state, task_sink=task_sink,
        )
        if pv_id is not None:
            last_pv = pv_id
    return last_pv


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
    confirm_uc: ConfirmPlanUseCase | None = None,
    task_sink: TaskSinkPort | None = None,
    state: FSMContext | None = None,
    history: ChatHistory | None = None,
    agent: PlannerAgent | None = None,
) -> None:
    if stt is None or message.voice is None or message.bot is None:
        await message.answer("Голосовые сообщения не поддерживаются — напиши текстом.")
        return
    bot = message.bot
    if message.voice.file_size and message.voice.file_size > _MAX_VOICE_BYTES:
        await message.answer("Голосовое слишком большое — пришли покороче или текстом.")
        return
    file = await bot.get_file(message.voice.file_id)
    if file.file_path is None:
        await message.answer("Не удалось получить голосовое сообщение — напиши текстом.")
        return
    audio = await bot.download_file(file.file_path)
    if audio is None:
        await message.answer("Не удалось скачать голосовое сообщение — напиши текстом.")
        return
    ack = await message.answer("🎙 Распознаю…")
    timed_out = False
    try:
        text = await asyncio.wait_for(
            stt.transcribe(audio.read(), "voice.ogg"), _STT_TIMEOUT_S
        )
    except TimeoutError:
        text, timed_out = None, True
    with contextlib.suppress(Exception):  # best-effort cleanup
        await ack.delete()
    if not text:
        await message.answer(
            "Долго распознаю — пришли покороче или текстом."
            if timed_out
            else "Не удалось распознать голос — напиши текстом."
        )
        return
    await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
        confirm_uc=confirm_uc, edit_state=state, task_sink=task_sink, history=history,
        agent=agent,
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
    confirm_uc: ConfirmPlanUseCase | None = None,
    task_sink: TaskSinkPort | None = None,
    state: FSMContext | None = None,
    history: ChatHistory | None = None,
    agent: PlannerAgent | None = None,
) -> None:
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Напиши, что нужно: /task <текст>.")
        return
    await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
        confirm_uc=confirm_uc, edit_state=state, task_sink=task_sink, history=history,
        agent=agent,
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
    confirm_uc: ConfirmPlanUseCase | None = None,
    task_sink: TaskSinkPort | None = None,
    history: ChatHistory | None = None,
) -> None:
    """FSM edit loop (spec flow step 14 / scenario J).

    Receives the manager's free-text edit instruction after they clicked
    "правка" on a proposed plan. Re-runs the intent parser and proposes a
    fresh plan. Edits ACCUMULATE: each fresh proposal re-arms the loop with the
    new pending plan, so several sequential edits all apply. The loop stays
    armed until the manager types «ок» (ConfirmIntent → commits and clears
    state inside ``_handle_text``).
    """
    text = (message.text or "").strip()
    if not text:
        return
    # Read the plan being edited *before* dispatching, so a typed «ок» can
    # commit it and a fresh proposal can supersede it.
    data = await state.get_data()
    old_pv_id = _parse_pv_id(data.get("pending_pv_id"))
    new_pv_id = await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
        confirm_uc=confirm_uc, last_pv_id=old_pv_id, edit_state=state,
        task_sink=task_sink, history=history,
    )
    # The edit produced a fresh proposal: retire the one it replaces so the
    # project list does not accumulate near-duplicate planning rows, then
    # re-arm the loop on the new proposal so the next edit also applies.
    if new_pv_id is not None and repo is not None:
        if old_pv_id is not None and old_pv_id != new_pv_id:
            old_pv = await repo.get_plan_version(old_pv_id)
            superseded = await repo.transition_plan_status(
                old_pv_id, "proposed", "superseded"
            )
            if superseded and old_pv is not None:
                await repo.set_project_status(old_pv.project_id, "cancelled")
        await state.set_state(PlanEditState.waiting)
        await state.update_data(pending_pv_id=str(new_pv_id))


def _parse_pv_id(raw: object) -> UUID | None:
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


@router.message(F.text & ~F.text.startswith("/"))
async def handle_mention_or_dm(
    message: Message,
    parser: IntentParserPort,
    actor: dict[str, Any],
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
    actor_record: PersonRecord | None = None,
    explain_uc: ExplainPlanUseCase | None = None,
    confirm_uc: ConfirmPlanUseCase | None = None,
    task_sink: TaskSinkPort | None = None,
    state: FSMContext | None = None,
    history: ChatHistory | None = None,
    agent: PlannerAgent | None = None,
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
        confirm_uc=confirm_uc, edit_state=state, task_sink=task_sink, history=history,
        agent=agent,
    )
