# Plan 006: Stop the regex parser from crashing on out-of-range dates

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Instead, open `src/planner/infra/llm/basic.py` and confirm
> the quoted excerpts match the live code. On any mismatch, treat it as a STOP
> condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

The regex fallback parser builds `datetime.date(...)` from regex-captured
numbers without validating ranges. Input like `"32.13"` matches the `_DDMM`
pattern and calls `date(year, 13, 32)`, which raises
`ValueError: month must be in 1..12` (QA report M3, reproduced live). In the
bot this is caught by the error-boundary middleware and degrades to a generic
error, but the user's message is lost rather than being treated as a normal
capture/clarify. The fix is to treat an out-of-range date as "no date found"
(`None`) so parsing continues gracefully. The same crash exists in the vacation
range path.

## Current state

`src/planner/infra/llm/basic.py`:

```python
# basic.py:37
def _parse_date(text: str, today: date) -> date | None:
    m = _ISO.search(text)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = _DDMM.search(text)
    if m:
        year = int(m[3]) if m[3] else today.year
        return date(year, int(m[2]), int(m[1]))
    m = _DM.search(text)
    if m:
        month = _month_num(m[2])
        if month:
            return date(today.year, month, int(m[1]))
    return None
```

```python
# basic.py:129
    def _vacation(self, text: str, ctx: ChatContext) -> Intent:
        person = _resolve_person(text, ctx)
        rng = _RANGE.search(text)
        if person and rng:
            month = _month_num(rng[3])
            if month:
                d_from = date(ctx.today.year, month, int(rng[1]))
                d_to = date(ctx.today.year, month, int(rng[2]))
                return VacationIntent(
                    person_name=person, day_from=d_from, day_to=d_to
                )
        return ClarifyIntent(question="Укажи имя и даты отпуска.")
```

The `_ISO`, `_DDMM`, `_DM` numbers and the `_RANGE` day numbers can all exceed
valid date ranges (`32`, month `13`, day `99`, etc.).

`tests/unit/test_basic_parser.py` is the test file. Note it imports `_parse_date`
directly: `from planner.infra.llm.basic import BasicIntentParser, _parse_date`.
Existing tests like `test_parse_date_ddmm_no_year` assert valid inputs still
resolve; keep them passing.

## Commands you will need

| Purpose   | Command                                              | Expected on success |
|-----------|------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/infra/llm/basic.py --strict`| exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                        | exit 0              |
| Tests     | `uv run pytest tests/unit/test_basic_parser.py -v`   | all pass            |

## Scope

**In scope**:
- `src/planner/infra/llm/basic.py` (`_parse_date` and `_vacation` only)
- `tests/unit/test_basic_parser.py`

**Out of scope** (do NOT touch):
- The regex patterns themselves (`_ISO`, `_DDMM`, `_DM`, `_RANGE`) — broadening
  them is not the fix; range-validating the constructed date is.
- The Claude parser (`infra/llm/claude.py`) — it uses a different path.
- Any other parser branch (load/project/what-if classification).

## Git workflow

- Branch: `advisor/006-parser-date-valueerror`
- Commit message: `fix(parser): treat out-of-range dates as no-date instead of crashing`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Guard date construction in _parse_date

Wrap each `date(...)` construction so an invalid combination returns `None`
instead of raising. Replace the body of `_parse_date`:

```python
def _parse_date(text: str, today: date) -> date | None:
    try:
        m = _ISO.search(text)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = _DDMM.search(text)
        if m:
            year = int(m[3]) if m[3] else today.year
            return date(year, int(m[2]), int(m[1]))
        m = _DM.search(text)
        if m:
            month = _month_num(m[2])
            if month:
                return date(today.year, month, int(m[1]))
    except ValueError:
        return None  # out-of-range numbers → treat as no date found
    return None
```

**Verify**: `uv run mypy src/planner/infra/llm/basic.py --strict` → exit 0

### Step 2: Guard date construction in _vacation

Update `_vacation` so an out-of-range range falls through to the clarify reply:

```python
    def _vacation(self, text: str, ctx: ChatContext) -> Intent:
        person = _resolve_person(text, ctx)
        rng = _RANGE.search(text)
        if person and rng:
            month = _month_num(rng[3])
            if month:
                try:
                    d_from = date(ctx.today.year, month, int(rng[1]))
                    d_to = date(ctx.today.year, month, int(rng[2]))
                except ValueError:
                    return ClarifyIntent(question="Укажи имя и корректные даты отпуска.")
                return VacationIntent(
                    person_name=person, day_from=d_from, day_to=d_to
                )
        return ClarifyIntent(question="Укажи имя и даты отпуска.")
```

**Verify**: `uv run ruff check src/planner/infra/llm/basic.py` → exit 0

### Step 3: Add regression tests

In `tests/unit/test_basic_parser.py`, add:

```python
def test_parse_date_out_of_range_ddmm_returns_none():
    """'32.13' is out of range — must return None, not raise ValueError."""
    assert _parse_date("дедлайн 32.13", date(2026, 6, 4)) is None


def test_parse_date_out_of_range_iso_returns_none():
    assert _parse_date("2026-99-99", date(2026, 6, 4)) is None


def test_capture_with_bad_date_does_not_crash():
    """A task-like message with a bad date is still captured (no crash)."""
    i = P.parse_sync("сделать отчёт к 99.99", CTX)
    assert i.kind == "capture_task"
    assert i.deadline is None


def test_vacation_out_of_range_returns_clarify():
    i = P.parse_sync("отпуск Айгуль 40-50 июня", CTX)
    assert i.kind == "clarify"
```

(`P` and `CTX` are already defined at the top of the file.)

**Verify**: `uv run pytest tests/unit/test_basic_parser.py -v` → all pass, including 4 new tests

## Test plan

- New tests: out-of-range `_DDMM` → None; out-of-range ISO → None; bad-date
  capture message still yields `capture_task` with `deadline=None`; out-of-range
  vacation range → `clarify`.
- Regression: all existing `test_parse_date_*` and `test_vacation_range` tests
  must still pass (valid dates unchanged).
- Verification: `uv run pytest tests/unit/test_basic_parser.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/infra/llm/basic.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/test_basic_parser.py -v` exits 0; 4 new tests pass
- [ ] `_parse_date("дедлайн 32.13", date(2026, 6, 4))` returns `None` (no exception)
- [ ] `git status --porcelain` lists only the two in-scope files as modified
- [ ] `plans/README.md` status row for 006 updated

## STOP conditions

Stop and report back if:

- `_parse_date` or `_vacation` no longer match the "Current state" excerpts.
- A test outside `test_basic_parser.py` depends on `_parse_date` raising
  `ValueError` (it should not — report if found).

## Maintenance notes

- This makes the parser forgiving: an unparseable date becomes "no date", and
  the message is still captured. That matches the module's stated low-friction
  philosophy ("missing fields simply stay empty").
- A reviewer should confirm valid dates are unaffected and that the `try` blocks
  wrap only the `date(...)` constructions, not the regex search/classification.
- Plan 008 also edits `basic.py` (project-delete misclassification). If both are
  executed, do 006 first (this plan), then 008 — the drift check protects you.
