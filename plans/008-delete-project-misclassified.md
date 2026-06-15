# Plan 008: Stop the regex parser classifying "delete project X" as create-project

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Instead, open `src/planner/infra/llm/basic.py` and confirm
> the quoted excerpt matches the live code. On any mismatch, treat it as a STOP
> condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 006 (both edit `basic.py`; execute 006 first)
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

In the regex fallback parser, a message like `удали проект «Альфа»`
("delete project «Alpha»") is classified as an **AddProjectIntent** — a create —
because the create branch only checks for the word "проект"/"project" and a
quoted title, with no check for delete phrasing (QA report M4). A manager asking
to remove a project could instead be offered to create one. The fix is to detect
delete/drop phrasing first and route it to the existing `WhatIfIntent`
`drop_project` operation (the project-removal intent the codebase already
models).

## Current state

`src/planner/infra/llm/basic.py` — the create branch, with no delete guard
before it:

```python
# basic.py:96
        if "что-если" in low or "что если" in low or low.startswith("whatif"):
            return self._what_if(text, ctx)

        if low in {"ок", "ok", "да", "yes", "confirm", "подтверждаю"}:
            return ConfirmIntent()

        if any(k in low for k in ("проект", "project", "новый проект", "add")):
            title_m = _QUOTE.search(text)
            if title_m:
                template: Literal["standard", "lite"] = (
                    "lite" if "lite" in low or "лайт" in low else "standard"
                )
                return AddProjectIntent(
                    title=title_m[1].strip(),
                    template_code=template,
                    deadline=_parse_date(text, ctx.today),
                )
```

The `_what_if` helper already supports `drop_project` (it checks for
`"убери"`, `"drop"`, `"удали проект"`), but it is only reached when the message
contains "что-если"/"что если"/"whatif". A bare "удали проект …" never reaches
it.

`WhatIfIntent` (from `planner.domain.intent`) has
`operation: Literal["shift_deadline", "add_person", "switch_to_lite", "drop_project"]`
and `project_title: str | None`.

Test file: `tests/unit/test_basic_parser.py`. `_QUOTE` is already imported
indirectly via the parser; the test uses `P.parse_sync(...)` and `CTX`.

## Commands you will need

| Purpose   | Command                                              | Expected on success |
|-----------|------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/infra/llm/basic.py --strict`| exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                        | exit 0              |
| Tests     | `uv run pytest tests/unit/test_basic_parser.py -v`   | all pass            |

## Scope

**In scope**:
- `src/planner/infra/llm/basic.py` (the `parse_sync` create branch only)
- `tests/unit/test_basic_parser.py`

**Out of scope** (do NOT touch):
- `_what_if` helper logic — it already classifies drop correctly; we just route
  to it / construct the drop intent.
- `_parse_date` (plan 006 owns that).
- The Claude parser.

## Git workflow

- Branch: `advisor/008-delete-project-misclassified`
- Commit message: `fix(parser): route "delete project X" to drop_project, not create`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add a delete-phrasing guard before the create branch

In `src/planner/infra/llm/basic.py`, inside `parse_sync`, insert a delete check
immediately BEFORE the `if any(k in low for k in ("проект", ...))` create
branch:

```python
        _delete_kw = ("удали", "удалить", "удаление", "убери", "drop", "delete")
        if "проект" in low and any(k in low for k in _delete_kw):
            title_m = _QUOTE.search(text)
            return WhatIfIntent(
                operation="drop_project",
                project_title=title_m[1].strip() if title_m else None,
            )

        if any(k in low for k in ("проект", "project", "новый проект", "add")):
            ...  # existing create branch unchanged
```

`WhatIfIntent` is already imported at the top of the file (used by `_what_if`).
Confirm the import is present; if not, add it to the existing
`from planner.domain.intent import (...)` block.

**Verify**: `uv run mypy src/planner/infra/llm/basic.py --strict` → exit 0

### Step 2: Add tests

In `tests/unit/test_basic_parser.py`, add:

```python
def test_delete_project_not_classified_as_create():
    i = P.parse_sync('удали проект «Альфа»', CTX)
    assert i.kind == "what_if"
    assert i.operation == "drop_project"
    assert i.project_title == "Альфа"


def test_remove_project_phrasing_drops():
    i = P.parse_sync('убери проект "Бета"', CTX)
    assert i.kind == "what_if"
    assert i.operation == "drop_project"


def test_create_project_still_works():
    """Guard must not break the normal create path."""
    i = P.parse_sync('создать проект "Гамма", шаблон standard', CTX)
    assert i.kind == "add_project"
    assert i.title == "Гамма"
```

**Verify**: `uv run pytest tests/unit/test_basic_parser.py -v` → all pass, including 3 new tests

## Test plan

- New tests: "удали проект «Альфа»" → `drop_project` with title; "убери проект"
  → `drop_project`; "создать проект" still → `add_project` (regression guard).
- Regression: existing `test_add_project_with_deadline`,
  `test_add_project_lite_backward_mode`, and `test_what_if_drop_project` must
  still pass.
- Verification: `uv run pytest tests/unit/test_basic_parser.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/infra/llm/basic.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/test_basic_parser.py -v` exits 0; 3 new tests pass
- [ ] `P.parse_sync('удали проект «Альфа»', CTX).operation == "drop_project"`
- [ ] Existing create-project tests still pass
- [ ] `git status --porcelain` lists only the two in-scope files as modified
- [ ] `plans/README.md` status row for 008 updated

## STOP conditions

Stop and report back if:

- The create branch no longer matches the "Current state" excerpt.
- `WhatIfIntent` no longer has a `drop_project` operation literal (the intent
  schema changed).
- Adding the delete guard breaks an existing what-if test in a way that
  indicates the keyword sets overlap unexpectedly — report which test.

## Maintenance notes

- `drop_project` via the bot is still admin-gated downstream (`what_if` is in
  `WRITE_KINDS`), so routing here does not bypass authorization — it only fixes
  classification.
- This is the regex *fallback* parser (used when no Anthropic key). The Claude
  parser classifies via the LLM and is unaffected. If the team always runs with
  a Claude key, this is low-impact — hence P3.
- A reviewer should confirm the delete guard sits before the create branch and
  that a plain "создать проект" is not caught by the delete keywords.
- Ordering: plan 006 also edits `basic.py`. Execute 006 first; the drift check
  protects you if it already landed.
