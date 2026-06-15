# Plan 021: clean process shutdown + a daily-summary job that can't die silently

> **Executor instructions**: Follow step by step; run every verification before
> moving on. On a "STOP condition", stop and report. Update this plan's row in
> `plans/README.md` when done.
>
> **Drift check (run first)**: working tree was dirty at authoring time. Open
> `src/planner/main.py` and `src/planner/infra/scheduler.py` and confirm the
> quoted lines match before editing. On a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-15

## Why this matters

The whole app is one process running the bot, the FastAPI admin, and the
APScheduler jobs in one event loop. Two resilience gaps:

1. **No graceful shutdown.** `main()` ends with a bare
   `await asyncio.gather(dp.start_polling(bot), server.serve())` — no
   `try/finally`, no signal handling, and the DB engine, bot HTTP session, and
   scheduler are never closed on any exit path. If `serve()` raises (port already
   bound, config error) the other coroutine is cancelled abruptly and the
   connection pool / sockets leak. Under a supervised restart loop those leaks
   compound on every crash.
2. **The one recurring job can fail invisibly.** `_daily_summary` calls
   `build_load_image` + `bot.send_photo`/`send_message` with no `try/except`, and
   `register_jobs` sets no `misfire_grace_time`, no `coalesce`, and registers no
   `EVENT_JOB_ERROR` listener. A transient Telegram outage or DB hiccup at 09:30
   means the team silently gets no summary, with no retry and no operator signal;
   if the process is down at fire time the run is skipped entirely.

## Current state

```python
# main.py:56-86
scheduler = AsyncIOScheduler()

async def _daily_summary() -> None:
    from datetime import date
    from aiogram.types import BufferedInputFile
    png = await build_load_image(repo, start=date.today())
    if png:
        await bot.send_photo(settings.team_chat_id,
            BufferedInputFile(png, filename="load.png"),
            caption="Дневная сводка нагрузки команды.")
    else:
        await bot.send_message(settings.team_chat_id, "Дневная сводка: активных планов нет.")

async def _refresh_calendar() -> None:
    return None

register_jobs(scheduler, SchedulerDeps(send_daily_summary=_daily_summary,
    refresh_calendar_snapshot=_refresh_calendar, timezone=settings.timezone))
scheduler.start()

log.info("running", web="http://0.0.0.0:8000", polling=True)
await asyncio.gather(dp.start_polling(bot), server.serve())   # <-- no cleanup
```

```python
# infra/scheduler.py:17-34
def register_jobs(scheduler: Any, deps: SchedulerDeps) -> None:
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(deps.send_daily_summary,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=deps.timezone),
        id="daily_load_summary", replace_existing=True)
    scheduler.add_job(deps.refresh_calendar_snapshot,
        CronTrigger(month=1, day=1, timezone=deps.timezone),
        id="refresh_calendar_snapshot", replace_existing=True)
```

Resources available to close: `engine` (`main.py:43`, `await engine.dispose()`),
`bot` (`await bot.session.close()`), `scheduler` (`scheduler.shutdown(wait=False)`).
Structured logger is `log` (`structlog`, `main.py:27`).

Existing tests mock the run loop: `tests/unit/test_main.py` asserts happy-path
wiring (start_polling/serve/scheduler.start called); `tests/unit/test_scheduler.py`
asserts the two job ids/funcs. Neither covers failure or cleanup.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Tests   | `uv run pytest tests/unit/test_main.py tests/unit/test_scheduler.py` | all pass |
| Types   | `mypy src/planner --strict` | exit 0 |
| Lint    | `ruff check .` | exit 0 |

## Scope

**In scope:**
- `src/planner/main.py`
- `src/planner/infra/scheduler.py`
- `tests/unit/test_main.py`
- `tests/unit/test_scheduler.py`

**Out of scope:**
- The renderers (`app/render/*`) — covered by plan 024.
- The `_refresh_calendar` no-op body — wiring the real snapshot refresh is
  already-DONE plan 013; do not implement it here.
- Changing the cron schedules themselves.

## Git workflow

- Branch: `advisor/021-lifecycle-scheduler-resilience`
- Conventional commits (e.g. `fix(main): dispose engine/bot/scheduler on shutdown; guard daily-summary job`).
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Wrap the run loop in try/finally with orderly teardown

In `main.py`, wrap the scheduler-start + gather in `try/finally`. In `finally`,
shut down in reverse order of creation, each guarded so one failure does not mask
the others:

```python
try:
    scheduler.start()
    log.info("running", web="http://0.0.0.0:8000", polling=True)
    await asyncio.gather(dp.start_polling(bot), server.serve())
finally:
    scheduler.shutdown(wait=False)
    await bot.session.close()
    await engine.dispose()
```

(If a teardown call can itself raise, log-and-continue so all three run.)

**Verify**: `uv run pytest tests/unit/test_main.py` → existing happy-path tests
still pass (they mock `start_polling`/`serve`; the finally block runs on normal
completion too). `mypy src/planner --strict` → exit 0.

### Step 2: Guard the daily-summary job body

Wrap the `_daily_summary` body in `try/except Exception` that logs a structured
error and does not re-raise (so a transient failure does not crash the
scheduler thread):

```python
async def _daily_summary() -> None:
    from datetime import date
    from aiogram.types import BufferedInputFile
    try:
        png = await build_load_image(repo, start=date.today())
        ...
    except Exception:
        log.exception("daily_summary_failed")
```

**Verify**: `uv run pytest tests/unit/test_main.py` → all pass.

### Step 3: Add misfire/coalesce + a job-error listener

In `infra/scheduler.py`:
- Pass `misfire_grace_time=3600` and `coalesce=True` to the `daily_load_summary`
  job so a brief downtime still fires once.
- After registering jobs, attach an error listener:

```python
from apscheduler.events import EVENT_JOB_ERROR
def _on_job_error(event: Any) -> None:
    import structlog
    structlog.get_logger("planner.scheduler").error(
        "scheduler_job_error", job_id=event.job_id, exc_info=event.exception)
scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
```

Keep it importable/testable — a small module-level function is fine.

**Verify**: `uv run pytest tests/unit/test_scheduler.py` → all pass.

### Step 4: Add resilience tests

- `test_main.py`: inject a `server.serve` AsyncMock that raises, call `main()`,
  and assert the teardown calls (`scheduler.shutdown`, `bot.session.close`,
  `engine.dispose`) were invoked. Follow the existing mocking style in that file.
- `test_scheduler.py`: register a job that raises, fire the `EVENT_JOB_ERROR`
  listener (call `_on_job_error` with a fake event), and assert it logs without
  raising. Also assert the daily job is registered with a non-`None`
  `misfire_grace_time`.

**Verify**: `uv run pytest tests/unit/test_main.py tests/unit/test_scheduler.py` → all pass.

## Test plan

- New tests in `test_main.py` (failure → cleanup) and `test_scheduler.py`
  (job-error listener + misfire config), following each file's existing
  mock-based style.
- Verification: `uv run pytest tests/unit/test_main.py tests/unit/test_scheduler.py` → all pass.

## Done criteria

ALL must hold:

- [ ] `main()` has a `finally` that calls `scheduler.shutdown`, `bot.session.close`, `engine.dispose`.
- [ ] `_daily_summary` body is wrapped in try/except that logs and does not re-raise.
- [ ] `daily_load_summary` job is registered with `misfire_grace_time` and `coalesce=True`; an `EVENT_JOB_ERROR` listener is attached.
- [ ] `uv run pytest tests/unit/test_main.py tests/unit/test_scheduler.py` → exit 0 with new cases.
- [ ] `ruff check .` → exit 0; `mypy src/planner --strict` → exit 0.
- [ ] No files outside the in-scope list modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

- `bot.session.close()` or `engine.dispose()` is not awaitable / not present on
  the objects as constructed — verify the actual aiogram/SQLAlchemy versions and
  report the correct teardown call instead of guessing.
- Adding the `finally` breaks an existing `test_main.py` mock that did not expect
  teardown calls — update that test to expect them (it was asserting the
  pre-fix behavior); if it's unclear, STOP and report.
- The installed APScheduler is 4.x (pyproject pins `>=3.10,<4.0`, so expect 3.x);
  if it is 4.x the listener API differs — report.

## Maintenance notes

- Optional follow-up (not in scope): install loop signal handlers
  (SIGTERM/SIGINT) for clean container stops.
- If more scheduled jobs are added, give each the same misfire/coalesce
  treatment and rely on the shared error listener.
- Reviewer: confirm all three teardown calls run even when one of them raises.
