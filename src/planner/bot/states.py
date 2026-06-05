"""Aiogram FSM states for the bot (spec section 8.1, flow step 14)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class PlanEditState(StatesGroup):
    """Active when a manager has clicked "правка" on a proposed plan."""
    waiting = State()
