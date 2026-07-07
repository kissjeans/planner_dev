"""Unit tests for the Notion project master-card sink (C3 / R6).

Pure block-building + matching logic, plus the HTTP paths (create/mark/archive)
against a faked ``httpx.AsyncClient`` — same style as ``test_notion_sink.py``.
"""

from datetime import date

import pytest

from planner.app.ports import SinkProject
from planner.infra.notion import project as mod
from planner.infra.notion.project import (
    NotionProjectSink,
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
    assert await sink.archive_card("page") is False


class _Resp:
    def __init__(self, data=None, code=200):
        self._data = data or {}
        self.status_code = code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http")


def _install_client(monkeypatch, *, get=None, post=None, patch=None):
    """Fake httpx.AsyncClient; each verb returns a _Resp or raises an Exception."""
    responses = {"get": get, "post": post, "patch": patch}
    calls: dict[str, list] = {"get": [], "post": [], "patch": []}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _make(method):
        async def _call(self, url, **kwargs):
            calls[method].append((url, kwargs))
            out = responses[method]
            if isinstance(out, Exception):
                raise out
            return out

        return _call

    for m in ("get", "post", "patch"):
        setattr(_Client, m, _make(m))

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    return calls


@pytest.mark.asyncio
async def test_create_card_under_page_parent_returns_id(monkeypatch):
    calls = _install_client(
        monkeypatch, get=_Resp(code=404), post=_Resp({"id": "pg-1"})
    )
    sink = NotionProjectSink("ntn_x", "parent1")
    assert await sink.create_card(_project()) == "pg-1"
    url, kwargs = calls["post"][0]
    assert url.endswith("/pages")
    assert kwargs["headers"]["Authorization"] == "Bearer ntn_x"
    assert kwargs["json"]["parent"] == {"page_id": "parent1"}
    title = kwargs["json"]["properties"]["title"]["title"][0]["text"]["content"]
    assert title == "Альфа (дедлайн 2026-07-01)"
    assert kwargs["json"]["children"] == _card_children(_project())


@pytest.mark.asyncio
async def test_create_card_under_database_parent_uses_schema_title_prop(monkeypatch):
    calls = _install_client(
        monkeypatch,
        get=_Resp({"properties": {"Название": {"type": "title"},
                                  "Дата": {"type": "date"}}}),
        post=_Resp({"id": "row-1"}),
    )
    sink = NotionProjectSink("ntn_x", "db1")
    assert await sink.create_card(_project()) == "row-1"
    _, kwargs = calls["post"][0]
    assert kwargs["json"]["parent"] == {"database_id": "db1"}
    assert "Название" in kwargs["json"]["properties"]


@pytest.mark.asyncio
async def test_create_card_database_without_title_prop_falls_back_to_name(monkeypatch):
    calls = _install_client(
        monkeypatch,
        get=_Resp({"properties": {"Дата": {"type": "date"}}}),
        post=_Resp({"id": "row-1"}),
    )
    sink = NotionProjectSink("ntn_x", "db1")
    assert await sink.create_card(_project()) == "row-1"
    _, kwargs = calls["post"][0]
    assert "Name" in kwargs["json"]["properties"]


@pytest.mark.asyncio
async def test_create_card_without_deadline_keeps_plain_title(monkeypatch):
    calls = _install_client(
        monkeypatch, get=_Resp(code=404), post=_Resp({"id": "pg-1"})
    )
    project = SinkProject(
        title="Альфа", template_code="standard",
        task_names=("Заполнение брифа",), deadline=None,
    )
    sink = NotionProjectSink("ntn_x", "parent1")
    assert await sink.create_card(project) == "pg-1"
    _, kwargs = calls["post"][0]
    assert kwargs["json"]["properties"]["title"]["title"][0]["text"]["content"] == "Альфа"


@pytest.mark.asyncio
async def test_create_card_resolves_parent_once(monkeypatch):
    calls = _install_client(
        monkeypatch, get=_Resp(code=404), post=_Resp({"id": "pg-1"})
    )
    sink = NotionProjectSink("ntn_x", "parent1")
    await sink.create_card(_project())
    await sink.create_card(_project())
    assert len(calls["get"]) == 1
    assert len(calls["post"]) == 2


@pytest.mark.asyncio
async def test_create_card_returns_none_on_api_error(monkeypatch):
    _install_client(monkeypatch, get=_Resp(code=404), post=_Resp(code=500))
    sink = NotionProjectSink("ntn_x", "parent1")
    assert await sink.create_card(_project()) is None


@pytest.mark.asyncio
async def test_create_card_returns_none_when_transport_down(monkeypatch):
    _install_client(monkeypatch, get=RuntimeError("down"))
    sink = NotionProjectSink("ntn_x", "parent1")
    assert await sink.create_card(_project()) is None


@pytest.mark.asyncio
async def test_mark_task_done_ticks_matching_todo(monkeypatch):
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": []}},
        {"type": "to_do", "id": "b2",
         "to_do": {"rich_text": [{"plain_text": "Дизайн презентации"}]}},
    ]
    calls = _install_client(
        monkeypatch, get=_Resp({"results": blocks}), patch=_Resp()
    )
    sink = NotionProjectSink("ntn_x", "parent1")
    assert await sink.mark_task_done("pg-1", "дизайн презентации") is True
    url, kwargs = calls["patch"][0]
    assert url.endswith("/blocks/b2")
    assert kwargs["json"] == {"to_do": {"checked": True}}


@pytest.mark.asyncio
async def test_mark_task_done_returns_false_when_todo_absent(monkeypatch):
    calls = _install_client(monkeypatch, get=_Resp({"results": []}))
    sink = NotionProjectSink("ntn_x", "parent1")
    assert await sink.mark_task_done("pg-1", "нет такой") is False
    assert calls["patch"] == []


@pytest.mark.asyncio
async def test_mark_task_done_returns_false_on_api_error(monkeypatch):
    _install_client(monkeypatch, get=RuntimeError("down"))
    sink = NotionProjectSink("ntn_x", "parent1")
    assert await sink.mark_task_done("pg-1", "задача") is False


@pytest.mark.asyncio
async def test_archive_card_patches_archived_flag(monkeypatch):
    calls = _install_client(monkeypatch, patch=_Resp())
    sink = NotionProjectSink("ntn_x", "parent1")
    assert await sink.archive_card("pg-1") is True
    url, kwargs = calls["patch"][0]
    assert url.endswith("/pages/pg-1")
    assert kwargs["json"] == {"archived": True}


@pytest.mark.asyncio
async def test_archive_card_returns_false_on_api_error(monkeypatch):
    _install_client(monkeypatch, patch=_Resp(code=500))
    sink = NotionProjectSink("ntn_x", "parent1")
    assert await sink.archive_card("pg-1") is False
