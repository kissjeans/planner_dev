"""Tests for ClaudeIntentParser with mocked API (spec 6.2)."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from planner.infra.llm.basic import BasicIntentParser
from planner.infra.llm.ports import ChatContext
from planner.infra.llm.prompts import (
    EXPLAIN_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    build_user_message,
)

# ---------------------------------------------------------------------------
# prompts.py coverage
# ---------------------------------------------------------------------------

def test_intent_system_prompt_nonempty():
    assert len(INTENT_SYSTEM_PROMPT) > 50


def test_explain_system_prompt_nonempty():
    assert len(EXPLAIN_SYSTEM_PROMPT) > 10


def test_build_user_message_includes_today():
    ctx = ChatContext(
        today=date(2026, 6, 5),
        known_people=["Андрей", "Айгуль"],
        aliases={"андрюха": "Андрей"},
        known_projects=["Альфа"],
    )
    msg = build_user_message("загрузи команду", ctx)
    assert "2026-06-05" in msg
    assert "Андрей" in msg
    assert "андрюха->Андрей" in msg
    assert "Альфа" in msg
    assert "загрузи команду" in msg


def test_build_user_message_empty_context():
    ctx = ChatContext(today=date(2026, 6, 5))
    msg = build_user_message("test", ctx)
    assert "2026-06-05" in msg
    assert "—" in msg  # empty people/aliases/projects render as dash


# ---------------------------------------------------------------------------
# ClaudeIntentParser — __init__ constructor (lines 29-34)
# ---------------------------------------------------------------------------

def test_constructor_builds_client():
    """ClaudeIntentParser.__init__ wires up instructor + anthropic clients."""
    with patch("instructor.from_anthropic") as mock_instr, \
         patch("anthropic.AsyncAnthropic") as mock_anth:
        mock_instr.return_value = MagicMock()
        mock_anth.return_value = MagicMock()
        from planner.infra.llm.claude import ClaudeIntentParser
        parser = ClaudeIntentParser(api_key="sk-test-key")
    assert mock_anth.called
    assert mock_instr.called
    assert isinstance(parser._fallback, BasicIntentParser)


def test_constructor_accepts_custom_fallback():
    with patch("instructor.from_anthropic"), patch("anthropic.AsyncAnthropic"):
        from planner.infra.llm.claude import ClaudeIntentParser
        custom = BasicIntentParser()
        parser = ClaudeIntentParser(api_key="sk-x", fallback=custom)
    assert parser._fallback is custom


# ---------------------------------------------------------------------------
# ClaudeIntentParser — parse() success path
# ---------------------------------------------------------------------------

def _make_parser():
    """Construct ClaudeIntentParser with fully mocked instructor + anthropic."""
    with patch("instructor.from_anthropic") as mock_instructor, \
         patch("anthropic.AsyncAnthropic") as mock_anthropic:
        mock_instructor.return_value = MagicMock()
        mock_anthropic.return_value = MagicMock()
        from planner.infra.llm.claude import ClaudeIntentParser
        parser = ClaudeIntentParser.__new__(ClaudeIntentParser)
        parser._client = MagicMock()
        parser._raw = MagicMock()
        parser._fallback = BasicIntentParser()
    return parser


@pytest.mark.asyncio
async def test_parse_returns_intent_from_api():
    from planner.domain.intent import LoadIntent

    parser = _make_parser()
    parser._client.messages.create = AsyncMock(return_value=LoadIntent())

    ctx = ChatContext(today=date(2026, 6, 5))
    result = await parser.parse("загрузка команды", ctx)
    assert result.kind == "load"


@pytest.mark.asyncio
async def test_parse_falls_back_on_api_error():
    """On any exception, must degrade to BasicIntentParser (spec §15)."""
    parser = _make_parser()
    parser._client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))

    ctx = ChatContext(today=date(2026, 6, 5))
    result = await parser.parse("загрузка команды", ctx)
    # BasicIntentParser recognises "загрузка" → LoadIntent
    assert result.kind == "load"


@pytest.mark.asyncio
async def test_explain_plan_returns_text():
    parser = _make_parser()
    block = MagicMock()
    block.type = "text"
    block.text = "Всё хорошо."
    resp = MagicMock()
    resp.content = [block]
    parser._raw.messages.create = AsyncMock(return_value=resp)

    result = await parser.explain_plan("план: 3 задачи")
    assert "Всё хорошо." in result


@pytest.mark.asyncio
async def test_explain_plan_skips_non_text_blocks():
    parser = _make_parser()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.text = "ignored"
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "OK"
    resp = MagicMock()
    resp.content = [tool_block, text_block]
    parser._raw.messages.create = AsyncMock(return_value=resp)

    result = await parser.explain_plan("summary")
    assert result == "OK"
