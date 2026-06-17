# Notion Task Sink Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD. Steps use checkbox (`- [ ]`) syntax. Preserve the existing hexagonal architecture (ports/adapters); do not redesign.

**Goal:** When a task is captured from Telegram, also create a page in the customer's Notion database — "Telegram → задача в Notion". Postgres stays the source of truth (the solver needs it); Notion is a best-effort mirror.

**Architecture:** A new `TaskSinkPort` (app boundary). Two adapters: `NullTaskSink` (no-op, used when Notion isn't configured — keyless degrade, matching the project's "empty key → fallback" pattern) and `NotionTaskSink` (httpx → Notion REST API). `CaptureTaskUseCase` dual-writes: Postgres first (unchanged), then the sink (best-effort; a Notion failure logs a warning and never breaks capture). The adapter **introspects the Notion DB schema** (`GET /v1/databases/{id}`) and maps task fields to whatever properties exist by type/name — so we don't hardcode the customer's column names.

**Tech Stack:** Python 3.12, httpx (already a dependency — no new package), Notion REST API `2022-06-28`, pydantic-settings, pytest. No DB migration (Notion is external).

---

## Inputs / config (runtime, not code)
- `NOTION_TOKEN` — the `ntn_…` internal-integration secret (env only).
- `NOTION_DATABASE_ID` — target tasks DB id (from the DB URL).
- The integration MUST be shared with that DB in Notion (Connections), else the API returns 403/404.
- If either is empty → `NullTaskSink` is wired and the bot behaves exactly as today (Postgres-only). No Notion calls.

## Field mapping (schema-aware, decided here)
The adapter fetches the DB `properties` once and maps:
- the single `title`-typed property ← `task_title` (required; always present in a Notion DB).
- first `date` property whose name matches /дедлайн|deadline|срок|due/i ← `deadline` (skip if no deadline).
- first `select|status` property whose name matches /статус|status/i ← a default value `"Новая"` (only if that value exists as an option; else skip).
- first property matching /исполнит|assignee|кому|ответствен/i ← assignee names, written as `rich_text` (we cannot resolve Telegram names to Notion `people` ids).
- first property matching /проект|project|клиент/i ← `project_title`, as `rich_text` or `select`.
- unknown/unmatched properties are left unset.
Mapping is pure and unit-testable from a schema dict + a captured task.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/planner/app/ports.py` | add `TaskSinkPort` Protocol + a `SinkTask` dataclass (the payload) |
| `src/planner/infra/notion/__init__.py` | package marker |
| `src/planner/infra/notion/mapping.py` | NEW — pure schema→properties mapper |
| `src/planner/infra/notion/client.py` | NEW — `NotionTaskSink` (httpx) + `NullTaskSink` |
| `src/planner/settings.py` | add `notion_token`, `notion_database_id` |
| `src/planner/app/capture_task.py` | dual-write to the sink (best-effort) |
| `src/planner/bot/runner.py` | build the sink, inject into capture flow |
| `src/planner/bot/handlers/task_router.py` | thread sink into capture; append Notion link to reply |
| `src/planner/main.py` | startup log `notion=on/off` |
| `.env.example` | document the two vars |
| `tests/unit/infra/test_notion_sink.py` | NEW — mapping + adapter (mocked httpx) + null sink |
| `tests/eval/test_notion_live.py` | NEW — opt-in live page-create (`RUN_NOTION_TEST=1`) |

---

## Task 1: settings + env

**Files:** `src/planner/settings.py`, `.env.example`; Test `tests/unit/test_settings.py`

- [ ] **Step 1: failing test** — append to `tests/unit/test_settings.py`:

```python
def test_settings_notion_defaults_empty(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.setenv("TEAM_CHAT_ID", "1")
    s = Settings(_env_file=None)
    assert s.notion_token == ""
    assert s.notion_database_id == ""
```

- [ ] **Step 2: run, expect FAIL** — `uv run pytest tests/unit/test_settings.py::test_settings_notion_defaults_empty -v` → AttributeError.

- [ ] **Step 3: implement** — in `src/planner/settings.py`, add under a `# Notion` section:

```python
    notion_token: str = ""
    """Notion internal-integration token (optional — Notion sync disabled when empty)."""

    notion_database_id: str = ""
    """Target Notion database id for captured tasks (optional)."""
```

Add to `.env.example`:

```
# Notion Configuration (optional — Telegram→Notion task mirror)
# Internal integration token; share the integration with the target DB.
NOTION_TOKEN=
NOTION_DATABASE_ID=
```

- [ ] **Step 4: run, expect PASS.** **Step 5: commit** `feat(settings): add NOTION_TOKEN + NOTION_DATABASE_ID`.

---

## Task 2: TaskSinkPort + SinkTask payload + NullTaskSink

**Files:** `src/planner/app/ports.py`, `src/planner/infra/notion/__init__.py`, `src/planner/infra/notion/client.py`; Test `tests/unit/infra/test_notion_sink.py`

- [ ] **Step 1: failing test** — create `tests/unit/infra/test_notion_sink.py`:

```python
import pytest
from planner.app.ports import SinkTask


@pytest.mark.asyncio
async def test_null_sink_returns_none():
    from planner.infra.notion.client import NullTaskSink
    out = await NullTaskSink().push_task(
        SinkTask(title="x", assignees=[], project=None, deadline=None)
    )
    assert out is None
```

- [ ] **Step 2: run, expect FAIL** (import errors).

- [ ] **Step 3: implement**

In `src/planner/app/ports.py` add:

```python
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class SinkTask:
    title: str
    assignees: list[str]
    project: str | None
    deadline: date | None


class TaskSinkPort(Protocol):
    async def push_task(self, task: SinkTask) -> str | None:
        """Mirror a captured task to an external sink. Returns a URL/id or None."""
        ...
```

Create `src/planner/infra/notion/__init__.py` (empty). Create `src/planner/infra/notion/client.py`:

```python
"""Notion task sink (spec §12 vNext — Telegram→Notion mirror)."""

from __future__ import annotations

import structlog

from planner.app.ports import SinkTask

log = structlog.get_logger(__name__)


class NullTaskSink:
    """No-op sink used when Notion is not configured (keyless degrade)."""

    async def push_task(self, task: SinkTask) -> str | None:
        return None
```

- [ ] **Step 4: run, expect PASS.** **Step 5: commit** `feat(notion): TaskSinkPort + SinkTask + NullTaskSink`.

---

## Task 3: schema-aware property mapper (pure)

**Files:** `src/planner/infra/notion/mapping.py`; Test `tests/unit/infra/test_notion_sink.py`

- [ ] **Step 1: failing tests** — append:

```python
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
```

- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement** `src/planner/infra/notion/mapping.py`:

```python
"""Pure mapper: Notion DB schema + SinkTask -> Notion page `properties` dict."""

from __future__ import annotations

import re

from planner.app.ports import SinkTask

_DEADLINE_RE = re.compile(r"дедлайн|deadline|срок|due", re.IGNORECASE)
_PROJECT_RE = re.compile(r"проект|project|клиент", re.IGNORECASE)
_ASSIGNEE_RE = re.compile(r"исполнит|assignee|кому|ответствен", re.IGNORECASE)


def _rich_text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": value}}]}


def _find(schema: dict, pattern: re.Pattern, types: tuple[str, ...]) -> str | None:
    for name, meta in schema.items():
        if meta.get("type") in types and pattern.search(name):
            return name
    return None


def build_properties(schema: dict, task: SinkTask) -> dict:
    props: dict = {}

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
```

- [ ] **Step 4: run, expect PASS.** **Step 5: commit** `feat(notion): schema-aware property mapper`.

---

## Task 4: NotionTaskSink adapter (httpx)

**Files:** `src/planner/infra/notion/client.py`; Test `tests/unit/infra/test_notion_sink.py`

**Context:** Notion REST: `GET https://api.notion.com/v1/databases/{id}` returns `{"properties": {name: {type,...}}}`; `POST https://api.notion.com/v1/pages` with `{"parent": {"database_id": id}, "properties": {...}}` creates the page and returns `{"id": "...", "url": "..."}`. Headers: `Authorization: Bearer <token>`, `Notion-Version: 2022-06-28`. Schema is fetched once and cached. Any error → log + return None (never raise).

- [ ] **Step 1: failing tests** — append (mock httpx.AsyncClient):

```python
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
```

- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement** — add to `src/planner/infra/notion/client.py`:

```python
import httpx

from planner.infra.notion.mapping import build_properties

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"
_TIMEOUT_S = 10.0


class NotionTaskSink:
    def __init__(self, token: str, database_id: str) -> None:
        self._token = token
        self._db = database_id
        self._schema: dict | None = None

    def _headers(self) -> dict:
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
                return r.json().get("url")
        except Exception as exc:  # noqa: BLE001 — Notion is a best-effort mirror
            log.warning("notion_push_failed", error=str(exc))
            self._schema = None  # force re-fetch next time
            return None
```

- [ ] **Step 4: run, expect PASS.** **Step 5: commit** `feat(notion): NotionTaskSink httpx adapter`.

---

## Task 5: dual-write in capture + wiring + reply link

**Files:** `src/planner/app/capture_task.py`, `src/planner/bot/runner.py`, `src/planner/bot/handlers/task_router.py`, `src/planner/main.py`; Tests `tests/unit/app/test_capture_task.py`, `tests/unit/bot/test_runner.py`

- [ ] **Step 1: failing tests**

In `tests/unit/app/test_capture_task.py` add a test: `CaptureTaskUseCase(repo, sink=<spy>).execute(...)` calls `sink.push_task` once with a `SinkTask` carrying title/assignees/project/deadline AFTER the repo write; and a test that a sink raising/returning None does NOT break the capture result.

In `tests/unit/bot/test_runner.py` add: with `notion_token` + `notion_database_id` set, the dispatcher wires a `NotionTaskSink`; without them, a `NullTaskSink`.

- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement**

`capture_task.py`: give `CaptureTaskUseCase.__init__(self, repo, sink: TaskSinkPort | None = None)`. After the existing repo writes + before returning `CaptureResult`, best-effort mirror:

```python
        notion_url: str | None = None
        if self._sink is not None:
            notion_url = await self._sink.push_task(
                SinkTask(
                    title=intent.task_title,
                    assignees=assignee_names,
                    project=project.title,
                    deadline=intent.deadline,
                )
            )
```

Add `notion_url: str | None` to `CaptureResult` (default None) and set it. (Repo writes are unchanged; the sink call is after them and cannot affect the DB.)

`runner.py`: build the sink and pass it where `CaptureTaskUseCase` is constructed:

```python
    from planner.infra.notion.client import NotionTaskSink, NullTaskSink
    sink = (
        NotionTaskSink(settings.notion_token, settings.notion_database_id)
        if settings.notion_token and settings.notion_database_id
        else NullTaskSink()
    )
    dp["task_sink"] = sink
```

`task_router.py`: thread `task_sink` into the capture path (`build_capture_reply` builds `CaptureTaskUseCase(repo, sink=task_sink)`); if `result.notion_url`, append a line `f"\n🔗 Notion: {result.notion_url}"` to the "✓ Записал" reply.

`main.py`: extend the startup log with `notion="on" if settings.notion_token and settings.notion_database_id else "off"`.

- [ ] **Step 4: run, expect PASS** (incl. full `uv run pytest -q`). **Step 5: commit** `feat: dual-write captured tasks to Notion (best-effort)`.

---

## Task 6: opt-in live Notion test

**Files:** `tests/eval/test_notion_live.py`; `pyproject.toml` marker (reuse `live`)

- [ ] **Step 1:** create `tests/eval/test_notion_live.py` — skipped unless `RUN_NOTION_TEST=1` AND `NOTION_TOKEN` AND `NOTION_DATABASE_ID` are set. When enabled, it builds a real `NotionTaskSink`, pushes a `SinkTask(title="ITP-smoke <ts>")`, and asserts a non-None URL is returned. Mark `pytestmark = pytest.mark.live`.

- [ ] **Step 2:** `uv run pytest tests/eval/test_notion_live.py -q` → SKIPPED by default (no creds, no flag). **Step 3: commit** `test: opt-in live Notion push smoke`.

---

## Task 7: verify + live smoke instructions

- [ ] **Step 1:** `uv run pytest -q` → all pass (live tests skipped). `uv run ruff check src tests` + `uv run mypy src` → clean.
- [ ] **Step 2 (manual, needs creds):** set `NOTION_TOKEN`/`NOTION_DATABASE_ID` in `.env`, share the integration with the DB, restart the bot, send a Telegram task → verify a page appears in the Notion DB and the bot reply includes the 🔗 Notion link. Then `RUN_NOTION_TEST=1 uv run pytest tests/eval/test_notion_live.py -v`.

---

## Notes / decisions
- **PG remains source of truth** — the solver/admin read Postgres; Notion is a one-way mirror of captured tasks. (Two-way sync is out of scope.)
- **Project-plan tasks** (from add_project) are NOT pushed in this phase — only the low-friction capture path. Add later if needed (same sink).
- **Assignees** are written as text, not Notion `people` (we can't resolve Telegram names → Notion user ids without a mapping).
- **Failure is silent to the user-critical path**: a Notion outage logs `notion_push_failed` and the task is still saved in Postgres; the reply simply omits the link.
- No new dependency (httpx already present). No DB migration.
