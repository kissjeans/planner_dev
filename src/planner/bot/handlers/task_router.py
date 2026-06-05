"""/task and @mention router (spec section 8.1).

Parses the message into an intent, enforces the write-gate, and — when the
solver/repo are wired — runs the AddProject flow and replies with the explained
plan. Without those deps it degrades to a human-readable interpretation.
"""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from planner.app.add_project import (
    AddProjectUseCase,
    InvalidProjectError,
    deserialize_allocations,
)
from planner.app.explain_plan import ExplainPlanUseCase
from planner.app.ports import PersonRecord, RepoPort
from planner.domain.intent import AddProjectIntent, ClarifyIntent, Intent
from planner.domain.permissions import can_execute
from planner.domain.solver.ports import SolverPort
from planner.infra.llm.ports import ChatContext, IntentParserPort
from planner.infra.stt.whisper import STTPort

router = Router(name="task")


async def build_add_project_reply(
    intent: AddProjectIntent,
    *,
    repo: RepoPort,
    solver: SolverPort,
    actor_record: PersonRecord,
    today: date,
    explain_uc: ExplainPlanUseCase | None = None,
) -> str:
    """Run the AddProject use-case and render the proposed plan as text."""
    template = await repo.get_project_template(intent.template_code)
    if template is None:
        return f"Шаблон «{intent.template_code}» не найден."

    people = await repo.get_solver_people()
    if not people:
        return "В команде нет активных людей — некому планировать."

    # Occupy capacity already taken by committed plans so the new project does
    # not double-book people (spec section 9, hard capacity constraint).
    existing: list = []
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
        return f"Не могу создать проект: {exc}"

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
    return f"Проект «{result.project.title}» — предложенный план:\n{summary}"


def describe_intent(intent: Intent) -> str:
    """Render a parsed intent as a short Russian confirmation line."""
    kind = intent.kind
    if kind == "add_project":
        when = intent.deadline.isoformat() if intent.deadline else "обратный режим (КП)"
        return f"Проект «{intent.title}», шаблон {intent.template_code}, дедлайн: {when}."
    if kind == "load":
        who = intent.person_name or "вся команда"
        return f"Загрузка: {who}."
    if kind == "what_if":
        return f"Что-если: {intent.operation}, проект {intent.project_title or '—'}."
    if kind == "vacation":
        return f"Отпуск {intent.person_name}: {intent.day_from}–{intent.day_to}."
    if kind == "confirm":
        return "Подтверждение последнего предложенного плана."
    if kind == "assign":
        return f"Назначить {intent.task_ref} на {intent.person_name}."
    return getattr(intent, "question", None) or "Не понял команду."


async def _handle_text(
    message: Message,
    text: str,
    parser: IntentParserPort,
    actor: dict,
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
        reply = await build_add_project_reply(
            intent,
            repo=repo,
            solver=solver,
            actor_record=actor_record,
            today=date.today(),
            explain_uc=explain_uc,
        )
        await message.answer(reply)
        return

    await message.answer(describe_intent(intent))


@router.message(F.voice)
async def handle_voice(
    message: Message,
    parser: IntentParserPort,
    actor: dict,
    stt: STTPort | None = None,
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
    actor_record: PersonRecord | None = None,
    explain_uc: ExplainPlanUseCase | None = None,
) -> None:
    if stt is None or message.voice is None:
        await message.answer("Голосовые сообщения не поддерживаются — напиши текстом.")
        return
    bot = message.bot
    file = await bot.get_file(message.voice.file_id)
    audio = await bot.download_file(file.file_path)
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
    actor: dict,
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
        message,
        text,
        parser,
        actor,
        repo=repo,
        solver=solver,
        actor_record=actor_record,
        explain_uc=explain_uc,
    )
