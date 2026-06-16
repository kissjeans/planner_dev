"""Live regression eval for intent parsing (opt-in).

Run with: RUN_LLM_EVAL=1 uv run pytest tests/eval -v
Skipped by default (no network/key spend in normal CI).
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from planner.infra.llm.ports import ChatContext

pytestmark = pytest.mark.live

_RUN = os.environ.get("RUN_LLM_EVAL") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))

# (phrase, expected_kind) — real commands incl. the reported failures.
CASES = [
    ("Сколько слотов у Раи?", "load"),
    ("Подскажи количество слотов, которые сейчас доступны у Rai.", "load"),
    ("Какое количество специалистов у нас сейчас свободно?", "load"),
    ("Загрузи команду", "load"),
    ("Рай уходит в отпуск с 1 июня по 26 августа", "vacation"),
    ("Оформить бриф по клиенту МТС, задача на Рая", "capture_task"),
    ("создай проект «Альфа», распланируй", "add_project"),
    ("ок", "confirm"),
    ("да, подтверждаю", "confirm"),
    ("Поставь Андрея и Рай сделать ресёрч по МТС через 3 дня", "capture_task"),
    ("Ты изменила загрузку других участников команды?", "load"),
]

PEOPLE = ("Андрей", "Рай", "Айгуль", "Лёша", "Катя", "Дима")
ALIASES = {"rai": "Рай", "раи": "Рай", "рай": "Рай"}


@pytest.mark.skipif(not _RUN, reason="set RUN_LLM_EVAL=1 and ANTHROPIC_API_KEY")
@pytest.mark.asyncio
@pytest.mark.parametrize("phrase,expected", CASES)
async def test_intent_eval(phrase, expected):
    from planner.infra.llm.claude import ClaudeIntentParser

    parser = ClaudeIntentParser(os.environ["ANTHROPIC_API_KEY"])
    ctx = ChatContext(today=date(2026, 6, 16), known_people=PEOPLE, aliases=ALIASES)
    out = await parser.parse(phrase, ctx)
    assert out.kind == expected, f"{phrase!r} -> {out.kind} (expected {expected})"
