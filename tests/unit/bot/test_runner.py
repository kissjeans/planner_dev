"""Tests for dispatcher/parser assembly (runner.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from planner.bot.runner import build_dispatcher, build_parser
from planner.infra.llm.basic import BasicIntentParser
from planner.settings import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        database_url="x",
        redis_url="redis://localhost:6380/0",
        bot_token="123:TEST",
        team_chat_id=1,
        anthropic_api_key="",
        jwt_secret="s",
        admin_ids="",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def mock_redis_storage():
    from aiogram.fsm.storage.memory import MemoryStorage
    # Patch include_router so module-level Router singletons can be "attached" repeatedly
    with patch("planner.bot.runner.RedisStorage") as m, \
         patch("aiogram.Dispatcher.include_router"):
        m.from_url.return_value = MemoryStorage()
        yield m


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------

def test_build_parser_no_key_returns_basic():
    settings = _settings(anthropic_api_key="")
    parser = build_parser(settings)
    assert isinstance(parser, BasicIntentParser)


def test_build_parser_with_key_returns_claude():
    settings = _settings(anthropic_api_key="sk-ant-test")
    with patch("planner.infra.llm.claude.ClaudeIntentParser") as mock_cls:
        mock_cls.return_value = MagicMock()
        parser = build_parser(settings)
    assert parser is not None


# ---------------------------------------------------------------------------
# build_dispatcher
# ---------------------------------------------------------------------------

def test_build_dispatcher_no_repo(mock_redis_storage):
    settings = _settings()
    parser = BasicIntentParser()
    dp = build_dispatcher(settings, parser)
    assert dp["parser"] is parser
    assert "repo" not in dp.workflow_data
    assert "solver" not in dp.workflow_data


def test_build_dispatcher_with_repo_wires_confirm_uc(mock_redis_storage):
    from planner.app.confirm_plan import ConfirmPlanUseCase

    settings = _settings()
    parser = BasicIntentParser()
    repo = MagicMock()
    dp = build_dispatcher(settings, parser, repo=repo)
    assert dp["repo"] is repo
    assert isinstance(dp["confirm_uc"], ConfirmPlanUseCase)


def test_build_dispatcher_with_solver(mock_redis_storage):
    settings = _settings()
    parser = BasicIntentParser()
    solver = MagicMock()
    dp = build_dispatcher(settings, parser, solver=solver)
    assert dp["solver"] is solver


def test_build_dispatcher_always_wires_stt(mock_redis_storage):
    settings = _settings()
    parser = BasicIntentParser()
    dp = build_dispatcher(settings, parser)
    assert "stt" in dp.workflow_data


def test_build_dispatcher_wires_chat_history(mock_redis_storage):
    """dp['history'] must be a ChatHistory so aiogram injects it by name."""
    from planner.infra.history import ChatHistory

    settings = _settings()
    dp = build_dispatcher(settings, BasicIntentParser())
    assert isinstance(dp["history"], ChatHistory)


def test_build_dispatcher_wires_notion_sink_when_configured(mock_redis_storage):
    from planner.infra.notion.client import NotionTaskSink

    settings = _settings(notion_token="ntn_x", notion_database_id="db1")
    dp = build_dispatcher(settings, BasicIntentParser())
    assert isinstance(dp["task_sink"], NotionTaskSink)


def test_build_dispatcher_wires_null_sink_when_unconfigured(mock_redis_storage):
    from planner.infra.notion.client import NullTaskSink

    settings = _settings(notion_token="", notion_database_id="")
    dp = build_dispatcher(settings, BasicIntentParser())
    assert isinstance(dp["task_sink"], NullTaskSink)


def test_build_dispatcher_no_key_no_agent(mock_redis_storage):
    """Without an API key the tool-use agent is not wired (legacy path only)."""
    settings = _settings(anthropic_api_key="")
    dp = build_dispatcher(settings, BasicIntentParser())
    assert "agent" not in dp.workflow_data


def test_build_dispatcher_agent_disabled_no_agent(mock_redis_storage):
    """agent_enabled=False keeps the legacy enum path even with a key."""
    settings = _settings(anthropic_api_key="sk-ant-test", agent_enabled=False)
    with patch("planner.infra.llm.agent.PlannerAgent") as mock_cls:
        mock_cls.return_value = MagicMock()
        dp = build_dispatcher(settings, BasicIntentParser())
    assert "agent" not in dp.workflow_data


def test_build_dispatcher_wires_agent_when_key_and_enabled(mock_redis_storage):
    """Key + agent_enabled → dp['agent'] is a singleton PlannerAgent."""
    settings = _settings(anthropic_api_key="sk-ant-test", agent_enabled=True)
    sentinel = MagicMock()
    with patch("planner.infra.llm.agent.PlannerAgent", return_value=sentinel) as mock_cls:
        dp = build_dispatcher(settings, BasicIntentParser())
    mock_cls.assert_called_once_with("sk-ant-test")
    assert dp["agent"] is sentinel


@pytest.mark.asyncio
async def test_set_bot_commands_registers_menu():
    """register_bot_commands sets the Telegram command menu (spec 8)."""
    from unittest.mock import AsyncMock

    from planner.bot.runner import register_bot_commands

    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    await register_bot_commands(bot)

    bot.set_my_commands.assert_awaited_once()
    (commands,) = bot.set_my_commands.call_args.args
    registered = {c.command for c in commands}
    assert {
        "start", "task", "load", "whatif", "vacation", "suggest", "replan"
    } <= registered


@pytest.mark.asyncio
async def test_run_builds_bot_and_polls(mock_redis_storage):
    """runner.py:67-70 — run() creates Bot + Dispatcher and starts polling."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch as _patch

    from planner.bot.runner import run

    settings = _settings(bot_token="123:TEST")
    with _patch("planner.bot.runner.Bot") as mock_bot_cls, \
         _patch("planner.bot.runner.build_parser") as mock_parser, \
         _patch("planner.bot.runner.build_dispatcher") as mock_dp:
        mock_parser.return_value = BasicIntentParser()
        mock_dp_inst = MagicMock()
        mock_dp_inst.start_polling = AsyncMock()
        mock_dp.return_value = mock_dp_inst
        await run(settings)
    mock_bot_cls.assert_called_once_with(token=settings.bot_token)
    assert mock_dp_inst.start_polling.called
