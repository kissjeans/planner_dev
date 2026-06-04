"""/whatif handler (spec section 8.1).

Sprint 4 baseline: parse the what-if intent and echo the interpretation. The
diff render against a committed plan is wired once the repo lands (Sprint 5).
"""

from __future__ import annotations

from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from planner.domain.intent import WhatIfIntent
from planner.domain.permissions import can_execute
from planner.infra.llm.ports import ChatContext, IntentParserPort

router = Router(name="whatif")


@router.message(Command("whatif"))
async def handle_whatif(
    message: Message, parser: IntentParserPort, actor: dict
) -> None:
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Опиши сценарий: /whatif <текст>.")
        return

    intent = await parser.parse(text, ChatContext(today=date.today()))
    if not isinstance(intent, WhatIfIntent):
        await message.answer("Это не похоже на сценарий «что-если». Переформулируй.")
        return
    if not can_execute(intent.kind, actor.get("is_admin", False)):
        await message.answer("Только админ может править план.")
        return

    target = intent.project_title or "—"
    await message.answer(
        f"Что-если: {intent.operation}, проект {target}. "
        "Дифф против зафиксированного плана появится в Спринте 5."
    )
