"""Tests for the /suggest capability hint handler (spec section 5)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from planner.app.ports import CapabilityRecord
from planner.bot.handlers.suggest import (
    build_suggestion_text,
    format_suggestions,
    handle_suggest,
)
from planner.domain.capability import AssigneeSuggestion
from tests.unit.app.conftest import FakeRepo


class _Answers:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, text: str, **kwargs: Any) -> None:
        self.calls.append(text)


def _message(text: str = "") -> tuple[SimpleNamespace, _Answers]:
    answers = _Answers()
    return SimpleNamespace(text=text, answer=answers), answers


def _cap(name, skills, *, external=False):
    return CapabilityRecord(
        person_id=uuid4(), name=name, skills=frozenset(skills), is_external=external
    )


# --- format_suggestions (pure) ---

def test_format_lists_coverage_and_load():
    s = AssigneeSuggestion(uuid4(), "Айгуль", 1.0, ("Копирайтинг",), (), 8)
    out = format_suggestions(["Копирайтинг"], (s,))
    assert "Айгуль" in out
    assert "100%" in out
    assert "8ч" in out


def test_format_shows_missing_skills():
    s = AssigneeSuggestion(uuid4(), "Лёша", 0.5, ("A",), ("B",), 0)
    out = format_suggestions(["A", "B"], (s,))
    assert "не хватает: B" in out


def test_format_empty_suggestions():
    out = format_suggestions(["Дизайн"], ())
    assert "не нашлось" in out


# --- build_suggestion_text (orchestration) ---

@pytest.mark.asyncio
async def test_build_text_ranks_and_formats():
    repo = FakeRepo()
    repo.capabilities = [
        _cap("Full", {"Копирайтинг", "Редактура"}),
        _cap("Partial", {"Копирайтинг"}),
    ]
    text = await build_suggestion_text(repo, "Копирайтинг, Редактура")
    assert text.index("Full") < text.index("Partial")


@pytest.mark.asyncio
async def test_build_text_usage_without_skills():
    repo = FakeRepo()
    text = await build_suggestion_text(repo, "   ")
    assert "/suggest" in text


# --- handler ---

@pytest.mark.asyncio
async def test_handle_suggest_answers():
    repo = FakeRepo()
    repo.capabilities = [_cap("Айгуль", {"Анализ рынка"})]
    msg, answers = _message("/suggest Анализ рынка")
    await handle_suggest(msg, repo=repo)
    assert len(answers.calls) == 1
    assert "Айгуль" in answers.calls[0]


@pytest.mark.asyncio
async def test_handle_suggest_no_repo():
    msg, answers = _message("/suggest Анализ рынка")
    await handle_suggest(msg, repo=None)
    assert "репозиторий не подключён" in answers.calls[0]
