# Plan 023: assignee-suggestion coverage stays within [0.0, 1.0]

> **Executor instructions**: Follow step by step; run every verification before
> moving on. On a "STOP condition", stop and report. Update this plan's row in
> `plans/README.md` when done.
>
> **Drift check (run first)**: working tree was dirty at authoring time. Open
> `src/planner/domain/capability.py` and confirm the quoted lines match before
> editing. On a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-15

## Why this matters

`suggest_assignees` ranks people for a task by skill `coverage` — documented as
"fraction of required skills the person has, [0.0, 1.0]" and used as the primary
sort key (`-coverage`). The numerator and denominator are deduplicated on
**different bases**: the numerator counts against a list deduped by *original
case*, while the denominator is a set deduped by *casefold*. When the required
skills contain a case/whitespace variant of the same skill (e.g. `"Копирайтинг"`
and `"копирайтинг"`), the numerator counts both but the denominator counts one,
so `coverage` can exceed 1.0 (verified: it returns 2.0). That breaks the
documented invariant, renders nonsense like "покрытие 200%" in chat
(`bot/handlers/suggest.py` formats `round(coverage*100)`), and distorts the
ranking so a candidate with an accidental duplicate in the query jumps ahead of
genuinely fuller matches.

## Current state

File: `src/planner/domain/capability.py` — pure domain logic.

```python
# capability.py:16-18
def _norm(skill: str) -> str:
    return skill.strip().casefold()

# capability.py:62-82  (inside suggest_assignees)
required = list(dict.fromkeys(s.strip() for s in required_skills if s.strip()))  # case-preserving dedup
required_keys = {_norm(s) for s in required}                                      # casefold dedup

suggestions = []
for c in candidates:
    if c.is_external and not include_external:
        continue
    have = {_norm(s) for s in c.skills}
    covered = tuple(s for s in required if _norm(s) in have)     # counted vs `required`
    missing = tuple(s for s in required if _norm(s) not in have)
    coverage = len(covered) / len(required_keys) if required_keys else 1.0  # denominator vs `required_keys`
    ...
```

`AssigneeSuggestion.coverage` is documented `[0.0, 1.0]` at capability.py:42.
The 9 existing tests in `tests/unit/domain/test_capability.py` use distinct
skills, so the mismatch never triggers.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Tests   | `uv run pytest tests/unit/domain/test_capability.py tests/unit/app/test_suggest_assignees.py tests/unit/bot/test_suggest.py` | all pass |
| Types   | `mypy src/planner --strict` | exit 0 |
| Lint    | `ruff check .` | exit 0 |

## Scope

**In scope:**
- `src/planner/domain/capability.py`
- `tests/unit/domain/test_capability.py`

**Out of scope:**
- `src/planner/app/suggest_assignees.py` and `bot/handlers/suggest.py` — they
  consume `coverage`; once it's bounded they are correct. (The separate
  load-double-count concern in `suggest_assignees.py` is already addressed by
  DONE plan 012 once merged — do not touch it here.)

## Git workflow

- Branch: `advisor/023-capability-coverage-invariant`
- Conventional commits (e.g. `fix(capability): single normalization basis so coverage stays in [0,1]`).
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Normalize numerator and denominator on one basis

Make the normalized key set the single source of truth, and keep a
key → display-label map so `covered_skills`/`missing_skills` still show
human-readable labels:

```python
required = list(dict.fromkeys(s.strip() for s in required_skills if s.strip()))
label_by_key = {}
for s in required:
    label_by_key.setdefault(_norm(s), s)   # first label wins for each key
required_keys = list(label_by_key)         # deduped on the SAME basis as counting

for c in candidates:
    if c.is_external and not include_external:
        continue
    have = {_norm(s) for s in c.skills}
    covered_keys = [k for k in required_keys if k in have]
    missing_keys = [k for k in required_keys if k not in have]
    coverage = len(covered_keys) / len(required_keys) if required_keys else 1.0
    covered = tuple(label_by_key[k] for k in covered_keys)
    missing = tuple(label_by_key[k] for k in missing_keys)
    ...
```

`coverage` is now `len(subset)/len(set)` over one basis → always within
`[0.0, 1.0]`. The empty-requirements case still yields `1.0`.

**Verify**: `mypy src/planner --strict` → exit 0; `uv run pytest tests/unit/domain/test_capability.py` → the 9 existing tests still pass (they use distinct skills, so labels/coverage are unchanged).

### Step 2: Add a regression test for duplicate-variant inputs

In `tests/unit/domain/test_capability.py` (model after the existing
`suggest_assignees` tests), add a case passing case/whitespace-variant
duplicates in `required_skills` (e.g. `["Копирайтинг", " копирайтинг "]`) against
a candidate that has `{"копирайтинг"}`, and assert:
- `coverage == 1.0` (not 2.0);
- `covered_skills` has exactly one entry;
- `missing_skills` is empty.

**Verify**: `uv run pytest tests/unit/domain/test_capability.py` → all pass, new case included.

## Test plan

- New regression test as above; verifies the invariant holds and labels dedupe.
- Verification: `uv run pytest tests/unit/domain/test_capability.py tests/unit/app/test_suggest_assignees.py tests/unit/bot/test_suggest.py` → all pass.

## Done criteria

ALL must hold:

- [ ] `coverage` computed from a single normalized key set; numerator and denominator share the basis.
- [ ] A regression test with duplicate-variant required skills asserts `coverage == 1.0`.
- [ ] `uv run pytest tests/unit/domain/test_capability.py` → exit 0 (existing 9 + new case).
- [ ] `ruff check .` → exit 0; `mypy src/planner --strict` → exit 0.
- [ ] No files outside the in-scope list modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

- An existing `test_capability.py` assertion on `covered_skills`/`missing_skills`
  ordering breaks because the label now comes from `label_by_key` — if the order
  differs, preserve the required-input order (iterate `required_keys` in input
  order, which the code above does) and report if a test still disagrees.

## Maintenance notes

- If skills ever become first-class entities with canonical names (not free
  strings), this normalization can be dropped in favor of comparing ids.
- Reviewer: confirm `covered_skills` still shows a readable label, not the
  casefolded key.
