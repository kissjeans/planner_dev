"""/load handler (spec section 8.1).

Sprint 3 baseline: parse the optional person filter and acknowledge. The PNG
heatmap render is added in Sprint 4 (LoadSummaryUseCase).
"""

from __future__ import annotations

from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from planner.infra.llm.ports import ChatContext, IntentParserPort

router = Router(name="load")


@router.message(Command("load"))
async def handle_load(message: Message, parser: IntentParserPort) -> None:
    text = (message.text or "").partition(" ")[2].strip() or "load"
    intent = await parser.parse(text, ChatContext(today=date.today()))
    who = getattr(intent, "person_name", None) or "вся команда"
    await message.answer(f"Загрузка ({who}): рендер появится в Спринте 4.")
