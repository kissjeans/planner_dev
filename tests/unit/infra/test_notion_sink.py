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
    from planner.infra.notion.mapping import build_properties
    from datetime import date
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
