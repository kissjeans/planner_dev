import pytest

from planner.app.ports import SinkTask


@pytest.mark.asyncio
async def test_null_sink_returns_none():
    from planner.infra.notion.client import NullTaskSink
    out = await NullTaskSink().push_task(
        SinkTask(title="x", assignees=[], project=None, deadline=None)
    )
    assert out is None


def test_mapping_sets_title_and_date():
    from datetime import date

    from planner.infra.notion.mapping import build_properties
    schema = {
        "Задача": {"type": "title"},
        "Дедлайн": {"type": "date"},
        "Проект": {"type": "rich_text"},
        "Исполнитель": {"type": "rich_text"},
    }
    props = build_properties(
        schema,
        SinkTask(title="бриф МТС", assignees=["Андрей"], project="МТС",
                 deadline=date(2026, 6, 20)),
    )
    assert props["Задача"]["title"][0]["text"]["content"] == "бриф МТС"
    assert props["Дедлайн"]["date"]["start"] == "2026-06-20"
    assert props["Проект"]["rich_text"][0]["text"]["content"] == "МТС"
    assert props["Исполнитель"]["rich_text"][0]["text"]["content"] == "Андрей"


def test_mapping_title_only_when_no_match():
    from planner.infra.notion.mapping import build_properties
    schema = {"Name": {"type": "title"}}
    props = build_properties(
        schema, SinkTask(title="t", assignees=[], project=None, deadline=None)
    )
    assert list(props.keys()) == ["Name"]


@pytest.mark.asyncio
async def test_notion_sink_creates_page(monkeypatch):
    from planner.infra.notion import client as mod

    calls = {}

    class _Resp:
        def __init__(self, data, code=200):
            self._data = data
            self.status_code = code
        def json(self):
            return self._data
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("http")

    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **k):
            return _Resp({"properties": {"Name": {"type": "title"}}})
        async def post(self, url, **k):
            calls["json"] = k["json"]
            return _Resp({"id": "p1", "url": "https://notion.so/p1"})

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    sink = mod.NotionTaskSink("ntn_x", "db1")
    url = await sink.push_task(
        SinkTask(title="бриф", assignees=[], project=None, deadline=None)
    )
    assert url == "https://notion.so/p1"
    assert calls["json"]["parent"]["database_id"] == "db1"
    assert calls["json"]["properties"]["Name"]["title"][0]["text"]["content"] == "бриф"


@pytest.mark.asyncio
async def test_notion_sink_degrades_on_error(monkeypatch):
    from planner.infra.notion import client as mod

    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise RuntimeError("down")
        async def post(self, *a, **k): raise RuntimeError("down")

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Boom)
    sink = mod.NotionTaskSink("ntn_x", "db1")
    out = await sink.push_task(SinkTask(title="x", assignees=[], project=None, deadline=None))
    assert out is None
