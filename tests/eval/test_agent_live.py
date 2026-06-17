"""Live agent eval for the tool-use PlannerAgent (opt-in, plan Task 4).

Run with: RUN_AGENT_EVAL=1 uv run pytest tests/eval/test_agent_live.py -v
Skipped by default — no network/key spend in normal CI.

This is the REAL gate the unit tests cannot be: the unit tests mock every
Anthropic turn (the model output is the risk), so they prove the loop wiring but
not that Claude actually decomposes a compound message, resolves assignees by
skill, or chains read→write tools. Here a real :class:`PlannerAgent` runs against
real Claude, while the ToolBox sits over the proven in-memory ``FakeRepo`` /
``FakeSolver`` doubles (no Postgres needed) — so we observe which tools the model
fires and that it returns non-empty Russian text, without persisting anywhere.

Each case wraps the ToolBox so ``execute`` is recorded; we assert the model
called a plausible subset of tools and produced a final reply.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from planner.app.add_project import ProjectTemplate, TemplateTaskSpec
from planner.app.ports import CapabilityRecord, PersonRecord, ProjectRecord
from planner.infra.llm.ports import ChatContext
from planner.infra.llm.tools import ToolBox

# Reuse the battle-tested doubles from the ToolBox unit suite (DRY — no second
# fake repo to drift). They already implement the full RepoPort surface the
# tools touch and never persist.
from tests.unit.infra.test_toolbox import FakeRepo, FakeSolver, _Person

pytestmark = pytest.mark.live

_RUN = os.environ.get("RUN_AGENT_EVAL") == "1" and bool(
    os.environ.get("ANTHROPIC_API_KEY")
)

# --- Seeded roster (small, deterministic) ---------------------------------

_ANDREY = _Person("Андрей", capacity_h=8)
_DIMA_ID = uuid4()
_OLEG_ID = uuid4()

PEOPLE = ("Андрей", "Дима", "Олег")
PROJECTS = ("Альфа",)


def _seeded_repo() -> FakeRepo:
    """A roster the agent can reason over: skills + one solver person + template."""
    template = ProjectTemplate(
        code="standard",
        tasks=(
            TemplateTaskSpec(
                ord=1, name="дизайн", duration_hours=8,
                allowed_person_ids=(_ANDREY.id,),
            ),
        ),
    )
    return FakeRepo(
        people=[
            PersonRecord(_ANDREY.id, "Андрей"),
            PersonRecord(_DIMA_ID, "Дима"),
            PersonRecord(_OLEG_ID, "Олег"),
        ],
        solver_people=[_ANDREY],
        projects=[ProjectRecord(uuid4(), "Альфа", "planning")],
        capabilities=(
            CapabilityRecord(person_id=_DIMA_ID, name="Дима", skills=frozenset({"дизайн"})),
            CapabilityRecord(person_id=_OLEG_ID, name="Олег", skills=frozenset({"аналитика"})),
        ),
        people_by_name={"Андрей": PersonRecord(_ANDREY.id, "Андрей")},
        template=template,
    )


class _RecordingToolBox(ToolBox):
    """ToolBox that records every tool name the model fired (eval observability)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.called: list[str] = []

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        self.called.append(name)
        return await super().execute(name, args)


def _ctx() -> ChatContext:
    return ChatContext(
        today=date(2026, 6, 17),
        known_people=PEOPLE,
        known_projects=PROJECTS,
        recent_messages=(),
    )


def _toolbox(repo: FakeRepo) -> _RecordingToolBox:
    return _RecordingToolBox(
        repo=repo,
        solver=FakeSolver(),
        actor={"is_admin": True},
        actor_record=PersonRecord(id=uuid4(), name="Менеджер", is_admin=True),
        task_sink=None,
    )


# (utterance, set of tools where at least one is expected to fire) — drawn from
# the acceptance utterances: compound+conditional, decompose, assign-by-skill.
CASES = [
    # Compound + conditional (the headline reported failure): read load, then —
    # if free — capture a task. We accept either the read alone or read→write.
    (
        "покажи загрузку Андрея и если свободен поставь задачу подготовить КП по МТС",
        {"get_team_load", "list_people", "capture_task"},
    ),
    # Decompose: several tasks in one message → capture_task per task.
    (
        "поставь две задачи: созвон с МТС и бриф по Билайну",
        {"capture_task"},
    ),
    # Assign-by-skill (MVP-08): no name given, only a skill → find_assignees.
    (
        "кому поручить дизайн? подбери по навыкам",
        {"find_assignees", "list_people"},
    ),
]


@pytest.mark.skipif(not _RUN, reason="set RUN_AGENT_EVAL=1 and ANTHROPIC_API_KEY")
@pytest.mark.asyncio
@pytest.mark.parametrize("utterance,expected_tools", CASES)
async def test_agent_live_calls_plausible_tools(utterance, expected_tools):
    from planner.infra.llm.agent import PlannerAgent

    agent = PlannerAgent(api_key=os.environ["ANTHROPIC_API_KEY"])
    toolbox = _toolbox(_seeded_repo())

    reply = await agent.run(utterance, _ctx(), toolbox)

    # The model must produce a final natural-language reply (never empty).
    assert reply.text.strip(), f"empty reply for {utterance!r}"
    # And it must have fired at least one of the plausible tools for the task.
    assert expected_tools & set(toolbox.called), (
        f"{utterance!r} fired {toolbox.called!r}, expected one of {expected_tools!r}"
    )
