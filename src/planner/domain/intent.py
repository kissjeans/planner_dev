"""Intent schema (spec section 6.1).

The LLM (or the regex fallback) maps a free-text / voice message to exactly
one of these Pydantic models. ``Intent`` is a discriminated union on ``kind``,
which lets ``instructor`` and the bot router branch without isinstance soup.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class AddProjectIntent(BaseModel):
    kind: Literal["add_project"] = "add_project"
    title: str
    template_code: Literal["standard", "lite"]
    deadline: date | None = None  # None => backward / critical-path mode
    brief_return_date: date | None = None
    notes: str | None = None


class LoadIntent(BaseModel):
    kind: Literal["load"] = "load"
    person_name: str | None = None  # None => whole team
    date_range: tuple[date, date] | None = None  # None => next 14 days

    @field_validator("date_range", mode="before")
    @classmethod
    def _coerce_range(cls, v: object) -> object:
        """Accept the LLM's {"from","to"} / {"start","end"} object form too."""
        if isinstance(v, dict):
            lo = v.get("from") or v.get("start")
            hi = v.get("to") or v.get("end")
            return (lo, hi) if lo and hi else None
        return v


class WhatIfIntent(BaseModel):
    kind: Literal["what_if"] = "what_if"
    operation: Literal[
        "shift_deadline", "add_person", "switch_to_lite", "drop_project"
    ]
    project_title: str | None = None
    new_deadline: date | None = None
    person_name: str | None = None


class VacationIntent(BaseModel):
    kind: Literal["vacation"] = "vacation"
    person_name: str
    day_from: date
    day_to: date
    capacity_h: int = 0  # 0 => full day off


class ConfirmIntent(BaseModel):
    kind: Literal["confirm"] = "confirm"
    plan_version_id: UUID | None = None  # None => latest proposed by context


class AssignIntent(BaseModel):
    kind: Literal["assign"] = "assign"
    task_ref: str  # e.g. "task 13 in project X"
    person_name: str


class CaptureTaskIntent(BaseModel):
    """Capture a task straight from a chat message into the DB (no interrogation).

    The primary low-friction path: a task-like message becomes a stored task
    immediately, with best-effort assignee / project / deadline. Missing fields
    stay null — the bot never loops asking for them.
    """

    kind: Literal["capture_task"] = "capture_task"
    task_title: str = Field(min_length=1, max_length=200)
    assignee_names: list[str] = Field(default_factory=list, max_length=10)
    project_name: str | None = Field(default=None, max_length=200)
    deadline: date | None = None
    # LLM-inferred enrichment (spec section 3, step 2). Both stay null/empty when
    # the model is unsure — the capture path never interrogates to fill them.
    est_hours: int | None = None
    required_skills: list[str] = Field(default_factory=list, max_length=20)


class ClarifyIntent(BaseModel):
    """Emitted when confidence is low — the bot asks a follow-up (flow step 8)."""

    kind: Literal["clarify"] = "clarify"
    question: str | None = None


Intent = Annotated[
    AddProjectIntent
    | LoadIntent
    | WhatIfIntent
    | VacationIntent
    | ConfirmIntent
    | AssignIntent
    | CaptureTaskIntent
    | ClarifyIntent,
    Field(discriminator="kind"),
]

# Intents that mutate state — gated to admins by the permissions middleware.
# capture_task writes (creates projects/tasks/assignments), so it is a write:
# spec rule is "writes are admin-only, reads are open" (domain/permissions.py).
# what_if is read-only (it re-solves in memory and never writes), so per spec
# section 16 it is open to everyone and excluded here.
WRITE_KINDS = frozenset(
    {"add_project", "vacation", "confirm", "assign", "capture_task"}
)
