"""Tests for PlannerAgent tool-use loop with a mocked Anthropic client (Task 2).

The model output is the risk, so we mock ``messages.create`` to drive the loop
deterministically: a ``tool_use`` turn followed by an ``end_turn`` text turn, an
iteration cap, and an API-error → BasicIntentParser fallback.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from planner.infra.llm.agent import AgentReply, PlannerAgent
from planner.infra.llm.basic import BasicIntentParser
from planner.infra.llm.ports import ChatContext

# --- mock builders ---------------------------------------------------------

def _tool_use_resp(name: str, args: dict, *, tool_id: str = "tu_1") -> MagicMock:
    """A stop_reason='tool_use' response carrying one tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = args
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


def _text_resp(text: str) -> MagicMock:
    """A stop_reason='end_turn' response carrying one text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _make_agent() -> PlannerAgent:
    """Construct PlannerAgent with a fully mocked anthropic client."""
    with patch("anthropic.AsyncAnthropic") as mock_anthropic:
        mock_anthropic.return_value = MagicMock()
        agent = PlannerAgent.__new__(PlannerAgent)
        agent._client = MagicMock()
        agent._fallback = BasicIntentParser()
    return agent


def _toolbox(execute_return: str = "Команда: • Андрей") -> MagicMock:
    tb = MagicMock()
    tb.execute = AsyncMock(return_value=execute_return)
    tb.last_proposed_pv_id = None
    return tb


def _ctx() -> ChatContext:
    return ChatContext(
        today=date(2026, 6, 17),
        known_people=("Андрей", "Айгуль"),
        known_projects=("Альфа",),
        recent_messages=("надо КП по МТС",),
    )


# --- constructor -----------------------------------------------------------

def test_constructor_builds_client():
    with patch("anthropic.AsyncAnthropic") as mock_anth:
        mock_anth.return_value = MagicMock()
        agent = PlannerAgent(api_key="sk-test-key")
    assert mock_anth.called
    assert isinstance(agent._fallback, BasicIntentParser)


def test_constructor_accepts_custom_fallback():
    with patch("anthropic.AsyncAnthropic"):
        custom = BasicIntentParser()
        agent = PlannerAgent(api_key="sk-x", fallback=custom)
    assert agent._fallback is custom


# --- tool-use loop ---------------------------------------------------------

@pytest.mark.asyncio
async def test_runs_tool_then_returns_final_text():
    """tool_use turn → end_turn: toolbox.execute is awaited, final text returned."""
    agent = _make_agent()
    agent._client.messages.create = AsyncMock(
        side_effect=[
            _tool_use_resp("list_people", {}),
            _text_resp("В команде Андрей и Айгуль."),
        ]
    )
    tb = _toolbox()

    reply = await agent.run("кто в команде?", _ctx(), tb)

    assert isinstance(reply, AgentReply)
    tb.execute.assert_awaited_once_with("list_people", {})
    assert reply.text == "В команде Андрей и Айгуль."
    assert reply.proposed_pv_id is None


@pytest.mark.asyncio
async def test_immediate_text_reply_no_tool():
    """A first end_turn response returns its text without any tool call."""
    agent = _make_agent()
    agent._client.messages.create = AsyncMock(return_value=_text_resp("Привет!"))
    tb = _toolbox()

    reply = await agent.run("привет", _ctx(), tb)

    assert reply.text == "Привет!"
    tb.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_block_precedes_message():
    """First user message embeds today/roster/projects then the raw message."""
    agent = _make_agent()
    create = AsyncMock(return_value=_text_resp("ok"))
    agent._client.messages.create = create

    await agent.run("поставь задачу", _ctx(), _toolbox())

    kwargs = create.await_args.kwargs
    first_user = kwargs["messages"][0]["content"]
    assert "2026-06-17" in first_user
    assert "Андрей" in first_user
    assert "Альфа" in first_user
    assert "---" in first_user
    assert first_user.rstrip().endswith("поставь задачу")
    assert kwargs["temperature"] == 0


@pytest.mark.asyncio
async def test_proposed_pv_id_propagated():
    """After the loop, AgentReply carries toolbox.last_proposed_pv_id."""
    agent = _make_agent()
    agent._client.messages.create = AsyncMock(
        side_effect=[
            _tool_use_resp("plan_project", {"title": "Бета", "template": "standard"}),
            _text_resp("Предложил план. Подтверждаешь?"),
        ]
    )
    pv = uuid4()
    tb = _toolbox(execute_return="План предложен.")
    tb.last_proposed_pv_id = pv

    reply = await agent.run("распланируй Бету", _ctx(), tb)

    assert reply.proposed_pv_id == pv


@pytest.mark.asyncio
async def test_iteration_cap_returns_cap_message():
    """If every turn is tool_use, the loop stops with the cap message."""
    agent = _make_agent()
    agent._client.messages.create = AsyncMock(
        return_value=_tool_use_resp("list_people", {})
    )
    tb = _toolbox()

    reply = await agent.run("бесконечно", _ctx(), tb)

    assert "переформулируй" in reply.text.lower()


@pytest.mark.asyncio
async def test_api_error_falls_back_to_basic_parser():
    """On any anthropic exception → fallback describe text, no raise."""
    agent = _make_agent()
    agent._client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
    tb = _toolbox()

    reply = await agent.run("покажи загрузку команды", _ctx(), tb)

    # BasicIntentParser recognises "загрузк" → LoadIntent; fallback text is non-empty.
    assert reply.text
    assert reply.proposed_pv_id is None
    tb.execute.assert_not_awaited()
