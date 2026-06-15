# Plan 004: Add per-user rate-limiting middleware to the bot

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Instead, open each file in "Current state" and confirm the
> quoted excerpt matches the live code. On any mismatch, treat it as a STOP
> condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (complements plan 003)
- **Category**: security
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

There is no rate-limiting anywhere in the bot (QA report H1). Even after the
known-sender gate (plan 003), a legitimate team member — or a compromised
account — can fire messages as fast as they can type, and each text triggers an
LLM parse call and potential DB writes. A simple per-user minimum-interval
throttle bounds the blast radius: messages arriving faster than the limit are
dropped before they reach the parser. The app runs as a single process
(README "one process, one Postgres writer"), so an in-memory throttle is
sufficient and avoids new infrastructure.

## Current state

- `src/planner/bot/runner.py` — `build_dispatcher` wires middlewares in order:
  error boundary, then actor resolution. New middleware is registered here:
  ```python
  # runner.py:57
      errors_mw = ErrorBoundaryMiddleware()
      dp.message.middleware(errors_mw)
      dp.callback_query.middleware(errors_mw)

      actor_mw = ActorMiddleware(settings.admin_id_set, repo)
      dp.message.middleware(actor_mw)
      dp.callback_query.middleware(actor_mw)

      dp.include_router(start.router)
      ...
  ```
- `src/planner/bot/middlewares/permissions.py` — the structural pattern to copy
  for a new middleware: a `BaseMiddleware` subclass whose `__call__` reads
  `data.get("event_from_user")` to find `tg_id`, then calls
  `await handler(event, data)`:
  ```python
  class ActorMiddleware(BaseMiddleware):
      def __init__(self, admin_ids: set[int], repo: RepoPort | None = None) -> None:
          ...
      async def __call__(self, handler, event, data):
          user = data.get("event_from_user")
          tg_id = user.id if user else None
          ...
          return await handler(event, data)
  ```
- `src/planner/bot/middlewares/__init__.py` exists (package marker).
- `tests/unit/bot/test_actor_middleware.py` — the test pattern for middlewares:
  a `_run(mw, tg_id)` helper builds `data = {"event_from_user": SimpleNamespace(id=tg_id)}`
  and awaits `mw(handler, object(), data)` with a stub `handler` that records
  whether it was called. Model the throttle tests on this.

## Commands you will need

| Purpose   | Command                                                              | Expected on success |
|-----------|----------------------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/bot/middlewares/throttle.py src/planner/bot/runner.py --strict` | exit 0, no errors |
| Lint      | `uv run ruff check src tests`                                        | exit 0              |
| Tests     | `uv run pytest tests/unit/bot/test_throttle_middleware.py tests/unit/bot/test_runner.py -v` | all pass |

## Scope

**In scope**:
- `src/planner/bot/middlewares/throttle.py` (create)
- `src/planner/bot/runner.py` (register the middleware)
- `tests/unit/bot/test_throttle_middleware.py` (create)

**Out of scope** (do NOT touch):
- `ActorMiddleware` / `ErrorBoundaryMiddleware` — leave their logic intact.
- Redis storage config — the throttle is intentionally in-memory (single
  process). Do not add a Redis-backed limiter.
- Any handler code.

## Git workflow

- Branch: `advisor/004-throttle-middleware`
- Commit message: `feat(security): add per-user throttle middleware to the bot`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Create the throttle middleware

Create `src/planner/bot/middlewares/throttle.py`:

```python
"""Per-user rate-limiting middleware (spec 15 risk: LLM cost / spam).

Single-process, in-memory minimum-interval throttle: messages from the same
Telegram user arriving faster than ``min_interval_s`` are dropped before they
reach the parser. Keyed by Telegram user id; events without a user pass through.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

_MIN_INTERVAL_S = 1.0  # at most one handled message per user per second


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, min_interval_s: float = _MIN_INTERVAL_S) -> None:
        self._min = min_interval_s
        self._last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        tg_id = user.id if user else None
        if tg_id is not None:
            now = time.monotonic()
            last = self._last_seen.get(tg_id)
            if last is not None and now - last < self._min:
                return None  # too soon — drop silently
            self._last_seen[tg_id] = now
        return await handler(event, data)
```

**Verify**: `uv run ruff check src/planner/bot/middlewares/throttle.py` → exit 0

### Step 2: Register the throttle in build_dispatcher

In `src/planner/bot/runner.py`, import the middleware and register it on
`message` (after the error boundary, before/with the actor middleware). Add the
import near the other middleware imports:

```python
from planner.bot.middlewares.throttle import ThrottleMiddleware
```

And in `build_dispatcher`, after the `errors_mw` registration and before
`actor_mw`:

```python
    throttle_mw = ThrottleMiddleware()
    dp.message.middleware(throttle_mw)
```

(Register on `dp.message` only — throttling button callbacks would harm UX and
they are cheap.)

**Verify**: `uv run mypy src/planner/bot/runner.py --strict` → exit 0

### Step 3: Add unit tests for the throttle

Create `tests/unit/bot/test_throttle_middleware.py`, modeled on
`test_actor_middleware.py`:

```python
"""Tests for ThrottleMiddleware per-user rate-limiting."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from planner.bot.middlewares.throttle import ThrottleMiddleware


async def _run(mw, tg_id):
    calls = {"n": 0}

    async def handler(event, data):
        calls["n"] += 1
        return "ok"

    data = {"event_from_user": SimpleNamespace(id=tg_id) if tg_id else None}
    result = await mw(handler, object(), data)
    return result, calls["n"]


@pytest.mark.asyncio
async def test_first_message_passes():
    mw = ThrottleMiddleware(min_interval_s=10.0)
    result, n = await _run(mw, 42)
    assert result == "ok" and n == 1


@pytest.mark.asyncio
async def test_second_message_within_interval_dropped():
    mw = ThrottleMiddleware(min_interval_s=10.0)
    await _run(mw, 42)
    result, n = await _run(mw, 42)  # immediately again
    assert result is None and n == 0  # handler not called the second time


@pytest.mark.asyncio
async def test_different_users_not_throttled_together():
    mw = ThrottleMiddleware(min_interval_s=10.0)
    await _run(mw, 1)
    result, n = await _run(mw, 2)
    assert result == "ok" and n == 1


@pytest.mark.asyncio
async def test_event_without_user_passes():
    mw = ThrottleMiddleware(min_interval_s=10.0)
    result, n = await _run(mw, None)
    assert result == "ok" and n == 1


@pytest.mark.asyncio
async def test_message_after_interval_passes():
    mw = ThrottleMiddleware(min_interval_s=0.0)  # no throttling window
    await _run(mw, 42)
    result, n = await _run(mw, 42)
    assert result == "ok" and n == 1
```

**Verify**: `uv run pytest tests/unit/bot/test_throttle_middleware.py -v` → all pass

### Step 4: Confirm the dispatcher still builds

`tests/unit/bot/test_runner.py` exercises `build_dispatcher`. Run it to confirm
the new middleware registration did not break wiring.

**Verify**: `uv run pytest tests/unit/bot/test_runner.py -v` → all pass

## Test plan

- New file `tests/unit/bot/test_throttle_middleware.py` (5 cases): first
  message passes; second within window dropped (handler not called); two
  different users independent; event with no user passes; message after the
  window passes. Model after `tests/unit/bot/test_actor_middleware.py`.
- Regression: `tests/unit/bot/test_runner.py` still passes (dispatcher wiring).
- Verification: `uv run pytest tests/unit/bot/test_throttle_middleware.py tests/unit/bot/test_runner.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/bot/middlewares/throttle.py src/planner/bot/runner.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/bot/test_throttle_middleware.py tests/unit/bot/test_runner.py -v` exits 0
- [ ] A test proves a same-user message inside the interval does NOT reach the handler
- [ ] `git status --porcelain` shows only the three in-scope files (two new, one modified)
- [ ] `plans/README.md` status row for 004 updated

## STOP conditions

Stop and report back if:

- `build_dispatcher` in `runner.py` no longer matches the "Current state"
  excerpt (middleware registration moved or changed shape).
- `test_runner.py` fails after registration and the failure is about middleware
  ordering you cannot resolve without changing other middlewares.
- aiogram's `BaseMiddleware.__call__` signature differs from the
  `ActorMiddleware` pattern shown (different aiogram major version).

## Maintenance notes

- The throttle is in-memory and per-process. If the app is ever scaled to
  multiple processes/instances, move the limiter to Redis (the storage is
  already configured) — note this is explicitly deferred here.
- `_last_seen` grows unbounded with distinct users over a long uptime. For a
  single-tenant team bot this is negligible; if it ever serves many users, add
  periodic eviction of stale entries.
- Tune `_MIN_INTERVAL_S` if 1s/user proves too strict for normal use.
- A reviewer should confirm callbacks are NOT throttled (button taps must stay
  responsive).
