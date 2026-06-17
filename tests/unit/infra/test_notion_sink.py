import pytest
from planner.app.ports import SinkTask


@pytest.mark.asyncio
async def test_null_sink_returns_none():
    from planner.infra.notion.client import NullTaskSink
    out = await NullTaskSink().push_task(
        SinkTask(title="x", assignees=[], project=None, deadline=None)
    )
    assert out is None
