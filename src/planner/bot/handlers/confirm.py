"""Confirm / edit callback handler (spec section 8.1, callbacks)."""

from __future__ import annotations

from uuid import UUID

from aiogram import F, Router
from aiogram.types import CallbackQuery

from planner.app.confirm_plan import (
    ConfirmPlanUseCase,
    PlanNotFoundError,
    PlanNotProposedError,
)
from planner.app.ports import PersonRecord

router = Router(name="confirm")


@router.callback_query(F.data.startswith("confirm:"))
async def handle_confirm(
    cb: CallbackQuery,
    actor: dict,
    confirm_uc: ConfirmPlanUseCase | None = None,
    actor_record: PersonRecord | None = None,
) -> None:
    if not actor.get("is_admin"):
        await cb.answer("Только админ может подтверждать план.", show_alert=True)
        return

    pv_id = UUID(cb.data.split(":", 1)[1])
    if confirm_uc is None or actor_record is None:
        # Repo wiring lands in Sprint 5; acknowledge for now.
        await cb.answer("План подтверждён.")
        return

    try:
        await confirm_uc.execute(pv_id, actor_record)
        await cb.answer("План зафиксирован.")
    except (PlanNotFoundError, PlanNotProposedError):
        await cb.answer("План не найден или уже зафиксирован.", show_alert=True)


@router.callback_query(F.data.startswith("edit:"))
async def handle_edit(cb: CallbackQuery) -> None:
    # Returns the user to the FSM edit loop (spec flow step 14).
    await cb.answer("Опиши правку текстом — соберу новый предложенный план.")
