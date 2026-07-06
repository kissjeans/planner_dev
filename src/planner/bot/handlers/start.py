"""/start handler (spec section 8.1)."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")

_GREETING = (
    "Привет! Я виртуальный планировщик команды.\n\n"
    "Команды:\n"
    "• /task <текст> — создать проект или задачу\n"
    "• /load [имя] — показать загрузку\n"
    "• /vacation — оформить отпуск\n"
    "• /replan — пересчитать план\n\n"
    "Можно писать голосом в ответ на моё сообщение."
)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(_GREETING)
