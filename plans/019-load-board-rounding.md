# Plan 019: the Load board reports true slot load (no round-before-ceil)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If a
> "STOP condition" occurs, stop and report — do not improvise. When done,
> update this plan's row in `plans/README.md`.
>
> **Drift check (run first)**: the working tree was dirty when this was written;
> a SHA diff is unreliable. Open `src/planner/app/admin_board.py` and confirm the
> quoted lines still match before editing. On a mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-15

## Why this matters

The Load tab tells admins how booked each person is, in slots (1 slot = 4h, see
`domain/slots.py`). The per-day load is computed by spreading a task's hours
across its calendar span, summing per person-day, then converting hours →
slots. But the conversion calls `round()` on the hour total **before** the
ceil-to-slots step. Rounding a fractional hour total down to a whole number
before ceiling defeats the ceiling: a person genuinely carrying load can read
as free, and days just over a slot boundary undercount. Admins then schedule
against numbers that hide real load — the exact failure the board exists to
prevent.

Worked examples (current behavior):
- A 6h task spread over 14 days → `6/14 = 0.43 h/day` → `round(0.43) = 0` →
  `hours_to_slots(0) = 0` slots **every day**. The person reads as idle.
- A person-day summing to 4.3h → `round(4.3) = 4` → `ceil(4/4) = 1` slot, but
  the true value is `ceil(4.3/4) = 2` slots. Overload bars and per-day totals
  are undercounted.

## Current state

File: `src/planner/app/admin_board.py` — `AdminBoardUseCase`, pure projection
logic (no IO). The defect is in the static `_load` helper:

```python
# admin_board.py:98  (inside build())  — fractional per-day hours
per_day_h = t.duration_hours / len(span) if span else 0
...
# admin_board.py:135-160  _load()
@staticmethod
def _load(people, person_day_h, days):
    totals = [0] * days
    rows = []
    for p in people:
        cap = hours_to_slots(p.capacity_h)
        slots = [
            hours_to_slots(round(person_day_h[(p.id, j)])) for j in range(days)  # <-- round() bug, line 146
        ]
        for j, s in enumerate(slots):
            totals[j] += s
        free = [max(cap - s, 0) for s in slots]
        overloaded = [s > cap for s in slots]
        available = cap * days
        pct = round(sum(slots) / available * 100) if available else 0   # line 153
        rows.append(LoadRow(name=p.name, capacity_slots=cap, slots=tuple(slots),
                            overloaded=tuple(overloaded), free=tuple(free), pct=pct))
    return rows, totals
```

`hours_to_slots` already accepts and correctly ceils any value ≥ 0
(`domain/slots.py:15`), but its parameter is typed `int`:

```python
# slots.py
SLOT_HOURS = 4
def hours_to_slots(hours: int) -> int:
    if hours <= 0:
        return 0
    return math.ceil(hours / SLOT_HOURS)
```

Because `mypy src/planner --strict` is clean today, passing a `float` to
`hours_to_slots` will fail typecheck unless the signature is widened.

Template context (so you don't over-engineer the pct decision):
`web/templates/load.html:24` renders `{{ row.pct }}%` as **plain text**, not a
progress-bar width. A value above 100 displays as e.g. "150%" — readable, not
visually broken.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Tests   | `uv run pytest tests/unit/app/test_admin_board.py tests/unit/test_slots.py` | all pass |
| Types   | `mypy src/planner --strict` | exit 0 |
| Lint    | `ruff check .` | exit 0 |

## Scope

**In scope:**
- `src/planner/app/admin_board.py`
- `src/planner/domain/slots.py` (only to widen the `hours_to_slots` signature to accept a float)
- `tests/unit/app/test_admin_board.py`
- `tests/unit/test_slots.py`

**Out of scope:**
- `src/planner/web/routes/board.py` and `web/templates/load.html` — the per-query
  and reassign issues there are handled by plan 020; do not touch them here.
- The Schedule/Calendar projections in the same file beyond what the fix needs.

## Git workflow

- Branch: `advisor/019-load-board-rounding`
- Message style: conventional commits (e.g. `fix(board): ceil true per-day hours so load is not undercounted`).
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Widen `hours_to_slots` to accept a float

In `domain/slots.py`, change the signature to `def hours_to_slots(hours: float) -> int:`.
The body is already correct (`hours <= 0` guard, `math.ceil`). Update the
docstring to say it accepts fractional hours.

**Verify**: `mypy src/planner --strict` → exit 0; `uv run pytest tests/unit/test_slots.py` → all pass.

### Step 2: Remove the premature `round()`

In `admin_board.py::_load`, line 146, change:

```python
slots = [hours_to_slots(round(person_day_h[(p.id, j)])) for j in range(days)]
```
to:
```python
slots = [hours_to_slots(person_day_h[(p.id, j)]) for j in range(days)]
```

**Verify**: `uv run pytest tests/unit/app/test_admin_board.py` → run; expect the
existing tests still pass (their fixtures use cleanly-dividing hours, per
`test_admin_board.py:30`). If any existing test now fails, that test encoded the
buggy rounding — STOP and report which assertion changed.

### Step 3: Decide the `pct` semantics (DEFAULT: keep raw, document it)

`pct` is window utilization and can exceed 100% for an overloaded person. Since
the template shows it as text, a "150%" reading is informative, not broken.
**Default: leave `pct` as-is** but add a one-line comment that values > 100 mean
overload. (Do NOT clamp unless the STOP condition below applies.)

**Verify**: no code change needed beyond the comment; `ruff check .` → exit 0.

### Step 4: Add characterization tests for the fractional path

In `tests/unit/app/test_admin_board.py` (model after the existing fixtures and
`test_load_*` tests there), add:
1. A multi-day task whose per-day hours are fractional but the day total is
   non-trivial (e.g. a 6h task assigned to one person over a span that lands ~4.3h
   on a given day) → assert that day's `slots` is the ceil of the true total, not 0
   and not undercounted.
2. An overloaded person (daily hours > capacity) → assert `overloaded[j] is True`
   and `pct` reflects the chosen semantics from Step 3.
3. Empty `tasks` and empty `people` → assert a well-formed `Board` with the right
   shapes (no exception, `totals` length == days).

**Verify**: `uv run pytest tests/unit/app/test_admin_board.py` → all pass, new
cases included.

## Test plan

- New tests live in `tests/unit/app/test_admin_board.py`, following the existing
  fixture style (build `TaskMeta`/`PersonRecord` lists, call
  `AdminBoardUseCase().build(...)`, assert on `board.load_rows`).
- Pattern reference: the existing `test_load_pct` and
  `test_task_span_clamped_to_window` in that file.
- Verification: `uv run pytest tests/unit/app` → all pass.

## Done criteria

ALL must hold:

- [ ] `grep -n "round(" src/planner/app/admin_board.py` shows no `round(` wrapping `person_day_h` (line 146 fixed).
- [ ] `hours_to_slots` signature accepts a float; `mypy src/planner --strict` → exit 0.
- [ ] `uv run pytest tests/unit/app/test_admin_board.py tests/unit/test_slots.py` → exit 0, with the 3 new cases present.
- [ ] `ruff check .` → exit 0.
- [ ] No files outside the in-scope list modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

- An existing `test_admin_board.py` assertion fails after Step 2 — it likely
  encoded the buggy rounding; report it rather than "fixing" the test silently.
- If the team decides the Load bar must be a clamped 0–100 progress bar (a
  template change), that is a different scope — STOP and report; do not redesign
  the template here.
- Removing `round()` causes a `mypy` error you cannot resolve by the Step 1
  signature change — report it.

## Maintenance notes

- If `load.html` is ever changed to a CSS width bar driven by `pct`, revisit
  Step 3 (then clamping to 100 becomes necessary).
- Watch for callers elsewhere that assumed `hours_to_slots` only took ints
  (none in `src/` today, but check after merging the unmerged plan batch).
- Reviewer should confirm the per-day totals (`board.totals`) now reflect the
  corrected slots.
