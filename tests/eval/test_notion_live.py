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

    # Clean up: archive the smoke page so live runs don't litter the board.
    import httpx

    page_id = url.rstrip("/").rsplit("-", 1)[-1]
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers={
                "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
                "Notion-Version": "2022-06-28",
            },
            json={"archived": True},
        )
        assert r.status_code == 200, f"cleanup failed: {r.text[:200]}"
