"""Pure mapper: Notion DB schema + SinkTask -> Notion page `properties` dict.

Schema-aware: matches the target DB's properties by name + Notion type, so it
fits real databases (e.g. multi_select assignees, a date column named "Дата",
a status/select column) without hardcoding column names.
"""

from __future__ import annotations

import re
from typing import Any

from planner.app.ports import SinkTask

_DATE_RE = re.compile(r"дата|дедлайн|deadline|срок|due", re.IGNORECASE)
_ASSIGNEE_RE = re.compile(r"assign|исполнит|кому|ответствен|assignee", re.IGNORECASE)
_PROJECT_RE = re.compile(r"проект|project|клиент|заказчик", re.IGNORECASE)
_STATUS_RE = re.compile(r"статус|status", re.IGNORECASE)


def _find(
    schema: dict[str, Any], pattern: re.Pattern[str], types: tuple[str, ...]
) -> str | None:
    """First property whose Notion type is in ``types`` and whose name matches.

    A name that STARTS with the token («Заказчик_new») wins over an embedded
    match («Тэг спецпроект» merely contains «проект»)."""
    candidates = [
        name for name, meta in schema.items()
        if meta.get("type") in types and pattern.search(name)
    ]
    if not candidates:
        return None
    prefixed = [name for name in candidates if pattern.match(name)]
    return (prefixed or candidates)[0]


def _first_of_type(schema: dict[str, Any], type_: str) -> str | None:
    return next((n for n, m in schema.items() if m.get("type") == type_), None)


def _text_value(prop_type: str, values: list[str]) -> dict[str, Any] | None:
    """Render string ``values`` into the right shape for a text-like property."""
    if prop_type == "multi_select":
        return {"multi_select": [{"name": v} for v in values]}
    if prop_type == "select":
        return {"select": {"name": values[0]}}
    if prop_type == "rich_text":
        return {"rich_text": [{"text": {"content": ", ".join(values)}}]}
    return None


def _snap_to_options(
    meta: dict[str, Any], values: list[str], strict: bool = False
) -> list[str]:
    """Snap each value to an existing select/multi_select option by token overlap
    (e.g. 'Рай' -> 'Рай Таиров'). Keep the value as-is when no option matches
    (Notion then creates a new option) — unless ``strict``, which drops it: some
    boards reject page creates that would add a new option. First match wins."""
    options = [o["name"] for o in meta.get(meta["type"], {}).get("options", [])]
    snapped: list[str] = []
    for value in values:
        low = value.lower()
        match = next(
            (o for o in options if low in o.lower() or o.lower() in low), None
        )
        if match is None and strict:
            continue
        snapped.append(match or value)
    return snapped


def build_properties(
    schema: dict[str, Any], task: SinkTask, strict: bool = False
) -> dict[str, Any]:
    props: dict[str, Any] = {}

    # Title — the single required property of a Notion DB.
    title_name = _first_of_type(schema, "title")
    if title_name:
        props[title_name] = {"title": [{"text": {"content": task.title}}]}

    # Deadline -> a date property (matched by name, else the first date column).
    # A time of day («встреча в 10:15») upgrades the value to a datetime so
    # Notion shows the hour on the card.
    if task.deadline:
        d = _find(schema, _DATE_RE, ("date",)) or _first_of_type(schema, "date")
        if d:
            start = task.deadline.isoformat()
            if task.time_start is not None:
                start = f"{start}T{task.time_start:%H:%M}:00"
            props[d] = {"date": {"start": start}}

    # Assignees -> multi_select / select / rich_text (names sent as-is; Notion
    # creates the option if it doesn't exist).
    if task.assignees:
        a = _find(schema, _ASSIGNEE_RE, ("multi_select", "select", "rich_text"))
        if a:
            names = task.assignees
            if schema[a]["type"] in ("multi_select", "select"):
                names = _snap_to_options(schema[a], names, strict=strict)
            value = _text_value(schema[a]["type"], names) if names else None
            if value:
                props[a] = value

    # Project / client -> multi_select / select / rich_text.
    if task.project:
        p = _find(schema, _PROJECT_RE, ("multi_select", "select", "rich_text"))
        if p:
            values = [task.project]
            if schema[p]["type"] in ("multi_select", "select"):
                values = _snap_to_options(schema[p], values, strict=strict)
            value = _text_value(schema[p]["type"], values) if values else None
            if value:
                props[p] = value

    # Status -> the first option of a status/select column (default for a new
    # task). Captured tasks start at whatever the board's first status is.
    s = _find(schema, _STATUS_RE, ("status", "select"))
    if s:
        s_type = schema[s]["type"]
        options = schema[s].get(s_type, {}).get("options", [])
        if options:
            props[s] = {s_type: {"name": options[0]["name"]}}

    return props
