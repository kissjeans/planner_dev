# Plan 013: Wire the isdayoff.ru calendar into startup and the yearly refresh job

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Open each file in "Current state" and confirm the quoted
> excerpts match the live code. On any mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug (stated-but-undelivered wiring)
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

The solver's working calendar is a `SnapshotCalendar` whose holiday set is a
**hardcoded 2026 list**. A complete, tested isdayoff.ru adapter exists
(`infra/calendar/isdayoff.py`) but is wired nowhere. The yearly
"refresh_calendar_snapshot" APScheduler job fires every Jan 1 and executes
`return None` — a no-op stub. Consequence: from January 2027 every plan is
computed against the wrong production calendar (2027 RU holidays treated as
working days) and nobody gets an error. This plan wires the adapter: fetch the
current + next year at startup (best-effort, snapshot fallback on failure) and
make the yearly job actually refresh the solver's calendar.

## Current state

- `src/planner/infra/calendar/snapshot.py` — the hardcoded snapshot:
  ```python
  RU_HOLIDAYS_2026: frozenset[date] = frozenset({ date(2026, 1, 1), ... })

  class SnapshotCalendar(WeekendCalendar):
      def __init__(self, holidays: frozenset[date] = RU_HOLIDAYS_2026) -> None:
          super().__init__(holidays)
  ```
- `src/planner/infra/calendar/isdayoff.py` — complete adapter:
  `IsDayOffClient(httpx.AsyncClient)` with `fetch_year_offdays(year)` and
  `build_snapshot(year)`; helpers `parse_year_offdays`, `holidays_from_offdays`.
  Note: `build_snapshot` covers ONE year; this plan adds a multi-year builder.
- `src/planner/main.py` — the stub and the wiring points:
  ```python
  # main.py:48
      solver = GreedySolver(SnapshotCalendar())
  ...
  # main.py:72
      async def _refresh_calendar() -> None:  # snapshot refresh hook (spec 11)
          return None
  ```
  `GreedySolver` stores the calendar as a public attribute
  (`self.calendar = calendar`, see `domain/solver/greedy.py:180-181`), so the
  refresh job can swap it by assignment.
- `src/planner/infra/scheduler.py` — registers `_refresh_calendar` on a Jan-1
  cron; no change needed there.
- `tests/unit/test_isdayoff.py` — existing test patterns for the adapter
  (read it before writing tests; model fakes on whatever client-stubbing it
  already uses).
- `tests/unit/test_main.py` — mocks everything in `main()` by attribute
  patching (`patch.object(main_mod, "SnapshotCalendar", ...)` etc. — see
  `_run_main_mocked` and the `with patch.object(...)` blocks). New names used
  by `main()` must be patched there or the tests will hit the network.

## Commands you will need

| Purpose   | Command                                                                          | Expected on success |
|-----------|----------------------------------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/infra/calendar src/planner/main.py --strict`            | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                                                    | exit 0              |
| Tests     | `uv run pytest tests/unit/test_isdayoff.py tests/unit/test_main.py -v`           | all pass            |

## Scope

**In scope**:
- `src/planner/infra/calendar/isdayoff.py` (add multi-year builder)
- `src/planner/main.py` (startup fetch + real refresh job)
- `tests/unit/test_isdayoff.py`
- `tests/unit/test_main.py` (patch the new call so tests stay offline)

**Out of scope** (do NOT touch):
- `snapshot.py` — the hardcoded 2026 set stays as the offline fallback.
- `scheduler.py` — job registration is correct.
- `domain/` — the solver and calendar rules are correct.

## Git workflow

- Branch: `advisor/013-wire-calendar-refresh`
- Commit message: `feat(calendar): wire isdayoff.ru fetch into startup and yearly refresh`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add a multi-year snapshot builder to isdayoff.py

In `src/planner/infra/calendar/isdayoff.py`:

```python
_HTTP_TIMEOUT_S = 10.0


async def fetch_snapshot_for_years(years: tuple[int, ...]) -> SnapshotCalendar:
    """Fetch holiday data for several years and merge into one snapshot.

    Opens its own bounded-timeout client; raises httpx errors to the caller —
    the caller decides whether to fall back to the offline snapshot.
    """
    holidays: frozenset[date] = frozenset()
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        idc = IsDayOffClient(client)
        for year in years:
            offdays = await idc.fetch_year_offdays(year)
            holidays = holidays | holidays_from_offdays(offdays)
    return SnapshotCalendar(holidays)
```

**Verify**: `uv run mypy src/planner/infra/calendar/isdayoff.py --strict` → exit 0

### Step 2: Use it in main() — startup + refresh job

In `src/planner/main.py`:

1. Import: `from planner.infra.calendar.isdayoff import fetch_snapshot_for_years`
   and add `from datetime import date as _date` is NOT needed — `date` is
   imported locally inside `_daily_summary`; for the year computation import
   `date` at module top level instead (move/add `from datetime import date`).
2. Add a small helper above `main()`:

```python
async def _load_calendar() -> SnapshotCalendar:
    """Live production calendar when isdayoff.ru is reachable, else snapshot."""
    year = date.today().year
    try:
        return await fetch_snapshot_for_years((year, year + 1))
    except Exception as exc:  # noqa: BLE001 — offline fallback by design (spec 10)
        log.warning("calendar_fetch_failed", error=str(exc))
        return SnapshotCalendar()
```

3. In `main()`, replace `solver = GreedySolver(SnapshotCalendar())` with:

```python
    solver = GreedySolver(await _load_calendar())
```

4. Replace the stub:

```python
    async def _refresh_calendar() -> None:
        solver.calendar = await _load_calendar()
        log.info("calendar_refreshed")
```

**Verify**: `uv run mypy src/planner/main.py --strict` → exit 0

### Step 3: Keep test_main offline

`tests/unit/test_main.py` patches `main_mod` attributes. Add to BOTH `with
patch.object(...)` blocks (in `test_main_wires_and_runs` and
`test_daily_summary_with_png`):

```python
        patch.object(main_mod, "_load_calendar", new=AsyncMock(return_value=MagicMock())),
```

Then add a test for the refresh hook + fallback:

```python
@pytest.mark.asyncio
async def test_load_calendar_falls_back_on_network_error():
    import planner.main as main_mod
    with patch.object(
        main_mod, "fetch_snapshot_for_years",
        new=AsyncMock(side_effect=RuntimeError("net down")),
    ):
        cal = await main_mod._load_calendar()
    from planner.infra.calendar.snapshot import SnapshotCalendar
    assert isinstance(cal, SnapshotCalendar)
```

And in `test_main_wires_and_runs` (or a sibling), after running `main()`,
exercise the captured `refresh_calendar_snapshot` dep and assert it no longer
no-ops silently — with `_load_calendar` patched to return a sentinel, calling
`deps.refresh_calendar_snapshot()` must swap `solver.calendar`. The solver in
that test is `MagicMock()` (patched `GreedySolver`), so assert the attribute
was assigned: after `await deps.refresh_calendar_snapshot()`, the mock solver's
`.calendar` equals the sentinel.

**Verify**: `uv run pytest tests/unit/test_main.py -v` → all pass

### Step 4: Unit-test the multi-year builder

In `tests/unit/test_isdayoff.py`, add a test for `fetch_snapshot_for_years`.
First read the file and reuse its existing client-stubbing approach. The
builder opens its own `httpx.AsyncClient`, so patch at the module level:

```python
@pytest.mark.asyncio
async def test_fetch_snapshot_for_years_merges_years(monkeypatch):
    from planner.infra.calendar import isdayoff as mod

    async def fake_fetch(self, year):
        return frozenset({date(year, 3, 2)})  # one weekday off-day per year

    monkeypatch.setattr(mod.IsDayOffClient, "fetch_year_offdays", fake_fetch)
    cal = await mod.fetch_snapshot_for_years((2026, 2027))
    assert not cal.is_working_day(date(2026, 3, 2))
    assert not cal.is_working_day(date(2027, 3, 2))
```

(Adjust the dates if those days are weekends — 2026-03-02 is a Monday and
2027-03-02 is a Tuesday, both weekdays, so they pass `holidays_from_offdays`.)

**Verify**: `uv run pytest tests/unit/test_isdayoff.py -v` → all pass

## Test plan

- New: multi-year merge test (Step 4); `_load_calendar` network-failure
  fallback test; refresh-job-swaps-calendar assertion (Step 3).
- Regression: all existing `test_isdayoff.py` and `test_main.py` tests pass,
  fully offline (no real HTTP — verify by running with network assumptions in
  mind; the tests must not hang).
- Verification: `uv run pytest tests/unit/test_isdayoff.py tests/unit/test_main.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/infra/calendar src/planner/main.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/test_isdayoff.py tests/unit/test_main.py -v` exits 0
- [ ] `grep -n "return None" src/planner/main.py` no longer shows the `_refresh_calendar` stub body
- [ ] A test proves fetch failure falls back to `SnapshotCalendar()` (no crash at startup)
- [ ] `git status --porcelain` lists only the four in-scope files as modified
- [ ] `plans/README.md` status row for 013 updated

## STOP conditions

Stop and report back if:

- `GreedySolver` no longer exposes `calendar` as a public attribute.
- `tests/unit/test_isdayoff.py` uses a stubbing approach incompatible with the
  monkeypatch above (e.g. vcrpy-only) — adapt to ITS pattern; if that is not
  possible offline, report.
- Startup ordering issues appear (e.g. `_load_calendar` needs the event loop in
  a way `main()` cannot provide) — report rather than restructuring `main()`.

## Maintenance notes

- Startup now performs one best-effort network call (≤10 s timeout, two GETs).
  If startup latency matters later, move the fetch into a background task that
  swaps the calendar when ready.
- The refresh job swaps `solver.calendar` by assignment — an intentional,
  single mutation point on a long-lived object; plans computed mid-swap use
  whichever calendar object they started with (each `plan()` call reads
  `self.calendar` once at entry — reviewer should confirm this stays true).
- The hardcoded `RU_HOLIDAYS_2026` fallback goes stale in 2027 — when the API
  is down at startup in 2027, schedules degrade to weekends-only. Acceptable
  fallback; consider persisting the last good fetch to disk as a follow-up.
