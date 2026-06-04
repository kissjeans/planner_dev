"""/task and @mention router (spec section 8.1).

Sprint 3 baseline: parse the message into an intent, enforce the write-gate,
and reply with a human-readable interpretation. Wiring intents to the
use-cases (AddProject, WhatIf, ...) lands in Sprint 4.
"""

from __future__ import annotations

from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from planner.domain.intent import ClarifyIntent, Intent
from planner.domain.permissions import can_execute
from planner.infra.llm.ports import ChatContext, IntentParserPort

router = Router(name="task")


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
    message: Message, text: str, parser: IntentParserPort, actor: dict
) -> None:
    ctx = ChatContext(today=date.today())
    intent = await parser.parse(text, ctx)

    if isinstance(intent, ClarifyIntent):
        await message.answer(describe_intent(intent))
        return

    if not can_execute(intent.kind, actor.get("is_admin", False)):
        await message.answer("Только админ может править план.")
        return

    await message.answer(describe_intent(intent))


@router.message(Command("task"))
async def handle_task(
    message: Message, parser: IntentParserPort, actor: dict
) -> None:
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Напиши, что нужно: /task <текст>.")
        return
    await _handle_text(message, text, parser, actor)
