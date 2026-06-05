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
        openai_api_key="",
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


def test_build_dispatcher_openai_key_wires_stt(mock_redis_storage):
    settings = _settings(openai_api_key="sk-openai-test")
    parser = BasicIntentParser()
    with patch("planner.infra.stt.whisper.WhisperSTT") as mock_stt:
        mock_stt.return_value = MagicMock()
        dp = build_dispatcher(settings, parser)
    assert "stt" in dp.workflow_data


def test_build_dispatcher_no_openai_no_stt(mock_redis_storage):
    settings = _settings(openai_api_key="")
    parser = BasicIntentParser()
    dp = build_dispatcher(settings, parser)
    assert "stt" not in dp.workflow_data
