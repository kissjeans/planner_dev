"""Confirm / edit callback handler (spec section 8.1, callbacks)."""

from __future__ import annotations

from contextlib import suppress
from typing import Any, cast
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from planner.app.confirm_plan import (
    ConfirmPlanUseCase,
    PlanNotFoundError,
    PlanNotProposedError,
)
from planner.app.ports import PersonRecord
from planner.bot.handlers.task_router import confirm_reply_text
from planner.bot.states import PlanEditState

router = Router(name="confirm")


async def _retire_keyboard(cb: CallbackQuery) -> None:
    """Strip the inline keyboard so the plan can't be confirmed twice.

    Tolerates an already-edited / too-old / deleted message (a stale second
    press): the TelegramBadRequest is swallowed, the keyboard is just gone.
    """
    msg = getattr(cb, "message", None)
    if not isinstance(msg, Message):
        return
    with suppress(TelegramBadRequest):
        await msg.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("confirm:"))
async def handle_confirm(
    cb: CallbackQuery,
    actor: dict[str, Any],
    confirm_uc: ConfirmPlanUseCase | None = None,
    actor_record: PersonRecord | None = None,
) -> None:
    if not actor.get("is_admin"):
        await cb.answer("Только админ может подтверждать план.", show_alert=True)
        return

    assert cb.data is not None
    pv_id = UUID(cb.data.split(":", 1)[1])
    if confirm_uc is None:
        await cb.answer("База данных не подключена.")
        return  # actor_record may be None — any chat member can confirm

    try:
        result = await confirm_uc.execute(pv_id, actor_record)
        await _retire_keyboard(cb)
        failed = getattr(result, "mirror_failed", 0)
        # A partial mirror needs the blocking alert; a clean commit only a toast.
        await cb.answer(confirm_reply_text(result), show_alert=bool(failed))
    except (PlanNotFoundError, PlanNotProposedError):
        # Already committed (e.g. a second press) — retire the button and say so
        # plainly instead of an alarming error.
        await _retire_keyboard(cb)
        await cb.answer("Этот план уже зафиксирован.", show_alert=True)


@router.callback_query(F.data.startswith("edit:"))
async def handle_edit(
    cb: CallbackQuery, state: FSMContext, actor: dict[str, Any]
) -> None:
    """Enter FSM edit loop (spec flow step 14): store plan_version_id, await edit text."""
    if not actor.get("is_admin"):
        await cb.answer("Только админ может править план.", show_alert=True)
        return

    assert cb.data is not None
    pv_id = cb.data.split(":", 1)[1]
    await state.set_state(PlanEditState.waiting)
    await state.update_data(pending_pv_id=pv_id)
    await cb.answer()
    await cast(Message, cb.message).answer(
        "Опиши правку — переформулируй запрос или напиши «что-если» операцию. "
        "Как будешь готов подтвердить — напиши «ок»."
    )
