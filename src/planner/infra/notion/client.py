"""Notion task sink (spec §12 vNext — Telegram→Notion mirror)."""

from __future__ import annotations

import structlog

from planner.app.ports import SinkTask

log = structlog.get_logger(__name__)


class NullTaskSink:
    """No-op sink used when Notion is not configured (keyless degrade)."""

    async def push_task(self, task: SinkTask) -> str | None:
        return None
