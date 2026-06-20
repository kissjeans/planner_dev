"""Unit tests for the Notion project master-card sink (C3 / R6).

Pure block-building + matching logic; no HTTP. The live HTTP path mirrors the
per-task sink and is exercised only against a real Notion workspace.
"""

from datetime import date

import pytest

from planner.app.ports import SinkProject
from planner.infra.notion.project import (
    NullProjectSink,
    _card_children,
    _find_todo,
)


def _project() -> SinkProject:
    return SinkProject(
        title="Альфа",
        template_code="standard",
        task_names=("Заполнение брифа", "Дизайн презентации"),
        deadline=date(2026, 7, 1),
    )


def test_card_has_checklist_brief_and_ideas():
    blocks = _card_children(_project())
    headings = [
        b["heading_2"]["rich_text"][0]["text"]["content"]
        for b in blocks
        if b["type"] == "heading_2"
    ]
    assert headings == ["Чеклист задач", "Бриф", "Идеи"]


def test_card_checklist_has_one_unchecked_todo_per_task():
    blocks = _card_children(_project())
    todos = [b for b in blocks if b["type"] == "to_do"]
    names = [t["to_do"]["rich_text"][0]["text"]["content"] for t in todos]
    assert names == ["Заполнение брифа", "Дизайн презентации"]
    assert all(t["to_do"]["checked"] is False for t in todos)


def test_find_todo_matches_case_insensitively():
    blocks = [
        {"type": "to_do", "id": "b1",
         "to_do": {"rich_text": [{"plain_text": "Заполнение брифа"}]}},
        {"type": "to_do", "id": "b2",
         "to_do": {"rich_text": [{"plain_text": "Дизайн презентации"}]}},
    ]
    found = _find_todo(blocks, "  дизайн презентации ")
    assert found is not None and found["id"] == "b2"


def test_find_todo_returns_none_when_absent():
    blocks = [{"type": "paragraph", "paragraph": {"rich_text": []}}]
    assert _find_todo(blocks, "Нет такой") is None


@pytest.mark.asyncio
async def test_null_sink_is_noop():
    sink = NullProjectSink()
    assert await sink.create_card(_project()) is None
    assert await sink.mark_task_done("page", "task") is False
