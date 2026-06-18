"""Aiogram FSM states for the bot (spec section 8.1, flow step 14)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class PlanEditState(StatesGroup):
    """Active when a manager has clicked "правка" on a proposed plan."""
    waiting = State()


class CaptureClarifyState(StatesGroup):
    """Active while collecting a task's missing key fields via buttons.

    Only the typed-answer steps need a state (new-project name / a custom date);
    button steps fire on callback_data regardless of state. The partial task
    args live in FSM data under ``capture``.
    """
    waiting_project_name = State()
    waiting_deadline_text = State()
