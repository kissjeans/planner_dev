"""Opt-in live Notion push smoke test.

Run with: RUN_NOTION_TEST=1 uv run pytest tests/eval/test_notion_live.py -v
Skipped by default (no real Notion API calls in normal CI).
Requires NOTION_TOKEN + NOTION_DATABASE_ID and the integration shared with the DB.
"""

from __future__ import annotations

import os
import time

import pytest

from planner.app.ports import SinkTask

pytestmark = pytest.mark.live

_RUN = (
    os.environ.get("RUN_NOTION_TEST") == "1"
    and bool(os.environ.get("NOTION_TOKEN"))
    and bool(os.environ.get("NOTION_DATABASE_ID"))
)


@pytest.mark.skipif(
    not _RUN, reason="set RUN_NOTION_TEST=1 with NOTION_TOKEN + NOTION_DATABASE_ID"
)
@pytest.mark.asyncio
async def test_notion_live_push_creates_page():
    from planner.infra.notion.client import NotionTaskSink

    sink = NotionTaskSink(
        os.environ["NOTION_TOKEN"], os.environ["NOTION_DATABASE_ID"]
    )
    url = await sink.push_task(
        SinkTask(
            title=f"ITP-smoke {int(time.time())}",
            assignees=[],
            project=None,
            deadline=None,
        )
    )
    assert url is not None
