"""/vacation handler (spec section 8.1, 5).

Parses VacationIntent from user text and calls SetVacationUseCase.
Falls back to acknowledgement when repo not wired.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from planner.app.ports import PersonRecord, RepoPort
from planner.app.set_vacation import PersonNotFoundError, SetVacationUseCase
from planner.domain.intent import VacationIntent
from planner.domain.permissions import can_execute
from planner.infra.llm.ports import ChatContext, IntentParserPort

router = Router(name="vacation")


@router.message(Command("vacation"))
async def handle_vacation(
    message: Message,
    parser: IntentParserPort,
    actor: dict[str, Any],
    repo: RepoPort | None = None,
    actor_record: PersonRecord | None = None,
) -> None:
    # Known-sender gate (same as task_router._handle_text): only resolved team
    # members or admins may act — and no LLM parse is spent on strangers.
    # When repo is None we are in degraded/echo mode (no DB) — skip the gate.
    if repo is not None and actor_record is None and not actor.get("is_admin", False):
        await message.answer(
            "Не узнал тебя — я отвечаю только участникам команды. "
            "Попроси администратора добавить тебя."
        )
        return

    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Укажи имя и даты: /vacation <имя> <дата_от> <дата_до>.")
        return

    intent = await parser.parse(text, ChatContext(today=date.today()))
    if not isinstance(intent, VacationIntent):
        await message.answer("Не понял формат. Пример: /vacation Айгуль 10 июня – 12 июня.")
        return

    if not can_execute(intent.kind, actor.get("is_admin", False)):
        await message.answer("Только админ может оформлять отпуск.")
        return

    if repo is None or actor_record is None:
        await message.answer(
            f"Отпуск {intent.person_name}: {intent.day_from}–{intent.day_to}. "
            "Репозиторий не подключён — изменения не сохранены."
        )
        return

    uc = SetVacationUseCase(repo)
    try:
        count = await uc.execute(
            intent, actor_record.id, is_admin=actor.get("is_admin", False)
        )
    except PermissionError as exc:
        await message.answer(str(exc))
        return
    except PersonNotFoundError:
        await message.answer(f"Сотрудник «{intent.person_name}» не найден.")
        return

    await message.answer(
        f"Отпуск {intent.person_name} {intent.day_from}–{intent.day_to} оформлен "
        f"({count} дн.). Перепланировка произойдёт при следующей заявке проекта."
    )
