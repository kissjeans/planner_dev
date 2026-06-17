"""Pure mapper: Notion DB schema + SinkTask -> Notion page `properties` dict."""

from __future__ import annotations

import re
from typing import Any

from planner.app.ports import SinkTask

_DEADLINE_RE = re.compile(r"дедлайн|deadline|срок|due", re.IGNORECASE)
_PROJECT_RE = re.compile(r"проект|project|клиент", re.IGNORECASE)
_ASSIGNEE_RE = re.compile(r"исполнит|assignee|кому|ответствен", re.IGNORECASE)


def _rich_text(value: str) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": value}}]}


def _find(
    schema: dict[str, Any], pattern: re.Pattern[str], types: tuple[str, ...]
) -> str | None:
    for name, meta in schema.items():
        if meta.get("type") in types and pattern.search(name):
            return name
    return None


def build_properties(schema: dict[str, Any], task: SinkTask) -> dict[str, Any]:
    props: dict[str, Any] = {}

    title_name = next(
        (n for n, m in schema.items() if m.get("type") == "title"), None
    )
    if title_name:
        props[title_name] = {"title": [{"text": {"content": task.title}}]}

    if task.deadline:
        d = _find(schema, _DEADLINE_RE, ("date",))
        if d:
            props[d] = {"date": {"start": task.deadline.isoformat()}}

    if task.project:
        p = _find(schema, _PROJECT_RE, ("rich_text",))
        if p:
            props[p] = _rich_text(task.project)
        else:
            ps = _find(schema, _PROJECT_RE, ("select",))
            if ps:
                props[ps] = {"select": {"name": task.project}}

    if task.assignees:
        a = _find(schema, _ASSIGNEE_RE, ("rich_text",))
        if a:
            props[a] = _rich_text(", ".join(task.assignees))

    return props
