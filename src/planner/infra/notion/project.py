"""Notion project master-card sink (C3 / R6).

On project creation a master card is built under a parent page: a task
checklist (to-do blocks), an empty "Бриф" section, and an "Идеи" sub-section
where the team writes ideas by hand. Status sync is one-way: marking a task
done ticks its checklist item. Best-effort and keyless-degrading, exactly like
the per-task mirror — Notion is a surface, Postgres is the source of truth.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from planner.app.ports import SinkProject

log = structlog.get_logger(__name__)

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"
_TIMEOUT_S = 10.0


class NullProjectSink:
    """No-op sink used when Notion is not configured (keyless degrade)."""

    async def create_card(self, project: SinkProject) -> str | None:
        return None

    async def mark_task_done(self, page_id: str, task_name: str) -> bool:
        return False


def _checklist_blocks(task_names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {
            "object": "block",
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "text": {"content": name}}],
                "checked": False,
            },
        }
        for name in task_names
    ]


def _heading(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _empty_paragraph() -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}


def _card_children(project: SinkProject) -> list[dict[str, Any]]:
    """Master-card body: checklist + empty brief + ideas placeholder."""
    blocks: list[dict[str, Any]] = [_heading("Чеклист задач")]
    blocks.extend(_checklist_blocks(project.task_names))
    blocks.append(_heading("Бриф"))
    blocks.append(_empty_paragraph())
    blocks.append(_heading("Идеи"))
    blocks.append(_empty_paragraph())
    return blocks


class NotionProjectSink:
    """Creates and updates project master cards under a Notion parent page."""

    def __init__(self, token: str, parent_page_id: str) -> None:
        self._token = token
        self._parent = parent_page_id
        # The parent may be a plain page OR a database (the task board). Resolved
        # once on first use; a DB needs parent={database_id} + its title prop.
        self._is_database: bool | None = None
        self._title_prop = "title"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        }

    async def _resolve_parent(
        self, c: httpx.AsyncClient
    ) -> tuple[dict[str, str], str]:
        """Return (parent, title_prop_name); detect page-vs-database once.

        When the configured id is a database (the task board), cards become rows
        in it with the schema's title property; otherwise child pages of a page.
        """
        if self._is_database is None:
            rdb = await c.get(
                f"{_API}/databases/{self._parent}", headers=self._headers()
            )
            self._is_database = rdb.status_code == 200
            if self._is_database:
                props = rdb.json().get("properties", {})
                self._title_prop = next(
                    (k for k, v in props.items() if v.get("type") == "title"), "Name"
                )
        if self._is_database:
            return {"database_id": self._parent}, self._title_prop
        return {"page_id": self._parent}, "title"

    async def create_card(self, project: SinkProject) -> str | None:
        title = project.title
        if project.deadline:
            title = f"{title} (дедлайн {project.deadline.isoformat()})"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
                parent, title_prop = await self._resolve_parent(c)
                r = await c.post(
                    f"{_API}/pages",
                    headers=self._headers(),
                    json={
                        "parent": parent,
                        "properties": {
                            title_prop: {
                                "title": [{"type": "text", "text": {"content": title}}]
                            }
                        },
                        "children": _card_children(project),
                    },
                )
                r.raise_for_status()
                page_id: str | None = r.json().get("id")
                return page_id
        except Exception as exc:  # noqa: BLE001 — Notion is a best-effort mirror
            log.warning("notion_card_failed", error=str(exc))
            return None

    async def mark_task_done(self, page_id: str, task_name: str) -> bool:
        """Find the to-do block whose text matches ``task_name`` and tick it."""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
                r = await c.get(
                    f"{_API}/blocks/{page_id}/children",
                    headers=self._headers(),
                )
                r.raise_for_status()
                block = _find_todo(r.json().get("results", []), task_name)
                if block is None:
                    return False
                upd = await c.patch(
                    f"{_API}/blocks/{block['id']}",
                    headers=self._headers(),
                    json={"to_do": {"checked": True}},
                )
                upd.raise_for_status()
                return True
        except Exception as exc:  # noqa: BLE001 — best-effort one-way mirror
            log.warning("notion_mark_done_failed", error=str(exc))
            return False


def _plain_text(rich: list[dict[str, Any]]) -> str:
    return "".join(part.get("plain_text", "") for part in rich).strip()


def _find_todo(blocks: list[dict[str, Any]], task_name: str) -> dict[str, Any] | None:
    target = task_name.strip().casefold()
    for b in blocks:
        if b.get("type") != "to_do":
            continue
        if _plain_text(b["to_do"].get("rich_text", [])).casefold() == target:
            return b
    return None
