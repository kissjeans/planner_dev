"""Notion task sink (spec §12 vNext — Telegram→Notion mirror)."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from planner.app.ports import SinkTask
from planner.infra.notion.mapping import build_properties

log = structlog.get_logger(__name__)

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"
_TIMEOUT_S = 10.0


class NullTaskSink:
    """No-op sink used when Notion is not configured (keyless degrade)."""

    async def push_task(self, task: SinkTask) -> str | None:
        return None


class NotionTaskSink:
    def __init__(self, token: str, database_id: str) -> None:
        self._token = token
        self._db = database_id
        self._schema: dict[str, Any] | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        }

    async def push_task(self, task: SinkTask) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
                if self._schema is None:
                    r = await c.get(f"{_API}/databases/{self._db}", headers=self._headers())
                    r.raise_for_status()
                    self._schema = r.json().get("properties", {})
                props = build_properties(self._schema, task)
                r = await c.post(
                    f"{_API}/pages",
                    headers=self._headers(),
                    json={"parent": {"database_id": self._db}, "properties": props},
                )
                r.raise_for_status()
                url: str | None = r.json().get("url")
                return url
        except Exception as exc:  # noqa: BLE001 — Notion is a best-effort mirror
            log.warning("notion_push_failed", error=str(exc))
            self._schema = None  # force re-fetch next time
            return None
