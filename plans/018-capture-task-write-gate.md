# Plan 018: capture_task is gated and validated like every other write path

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: this repo's working tree was dirty when the plan
> was written, so a SHA diff is unreliable. Instead, open each file in
> "Current state" and confirm the quoted lines still match before editing. On a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `442c9a4`, 2026-06-15

## Why this matters

The bot's whole authorization model is "writes are admin-only, reads are open"
(`domain/permissions.py:1`). Every state-changing intent is listed in
`WRITE_KINDS` and gated by `can_execute()`. The newer `capture_task` path is a
write — it creates projects, creates tasks, assigns people, and writes audit
rows — but it is **not** in `WRITE_KINDS` and its handler branch runs and
returns *before* the `can_execute()` gate is ever reached. Net effect: any
sender the bot will talk to (including a non-admin team member) can mutate the
planning database by phrasing a message as a task. That silently contradicts
the documented policy and lets unprivileged input spawn arbitrary projects and
tasks. There is also no validation on the captured text, so a blank or
arbitrarily long title becomes a stored row.

## Current state

Files:

- `src/planner/domain/intent.py` — the `Intent` union and the `WRITE_KINDS`
  set. `capture_task` is **missing** from it:

  ```python
  # intent.py:95-98
  # Intents that mutate state — gated to admins by the permissions middleware.
  WRITE_KINDS = frozenset(
      {"add_project", "what_if", "vacation", "confirm", "assign"}
  )
  ```

  `CaptureTaskIntent` (intent.py:61-73) has bare-`str` fields with no
  constraints:

  ```python
  kind: Literal["capture_task"] = "capture_task"
  task_title: str
  assignee_name: str | None = None
  project_name: str | None = None
  deadline: date | None = None
  ```

- `src/planner/bot/handlers/task_router.py` — `_handle_text` (lines 144-193).
  The capture branch returns before the gate:

  ```python
  # task_router.py:162-173
  if isinstance(intent, CaptureTaskIntent):
      if repo is None:
          await message.answer(describe_intent(intent))
          return
      await message.answer(
          await build_capture_reply(intent, repo=repo, actor_record=actor_record)
      )
      return                                      # <-- returns BEFORE the gate

  if not can_execute(intent.kind, actor.get("is_admin", False)):
      await message.answer("Только админ может править план.")
      return
  ```

- `src/planner/domain/permissions.py` — the gate; anything not in `WRITE_KINDS`
  is open to everyone:

  ```python
  def can_execute(kind: str, is_admin: bool) -> bool:
      if kind in WRITE_KINDS:
          return is_admin
      return True
  ```

- `src/planner/app/capture_task.py` — `CaptureTaskUseCase.execute` writes
  unconditionally: `create_project` (line 39), `create_task` (line 51),
  `assign_task` (line 63), `add_audit` (line 66). It uses
  `name or INBOX_PROJECT`, so a whitespace-only `project_name` is truthy and
  spawns a stub project (line 35).

Convention to follow: the gate is enforced in the handler (`task_router.py`),
exactly as the other write intents are. Match that — do not push permission
logic down into the use-case.

**Tests that currently cement the bypass (must be updated, not worked around):**

- `tests/unit/test_intent.py:66` asserts `"capture_task" not in WRITE_KINDS`
  with a comment "capture open to everyone".
- `tests/unit/bot/test_handler_coverage.py:165` (`test_handle_text_capture_writes_to_db`)
  runs the capture branch with `{"is_admin": False}` and asserts the write
  succeeds.

## Commands you will need

| Purpose   | Command                                                        | Expected on success |
|-----------|----------------------------------------------------------------|---------------------|
| Tests     | `uv run pytest tests/unit/test_intent.py tests/unit/bot/test_handler_coverage.py tests/unit/app/test_capture_task.py` | all pass |
| Full unit | `uv run pytest tests/unit`                                     | all pass            |
| Lint      | `ruff check .`                                                 | exit 0              |
| Types     | `mypy src/planner --strict`                                    | exit 0, no errors   |

## Scope

**In scope:**
- `src/planner/domain/intent.py`
- `src/planner/bot/handlers/task_router.py`
- `src/planner/app/capture_task.py`
- `tests/unit/test_intent.py`
- `tests/unit/bot/test_handler_coverage.py`
- `tests/unit/app/test_capture_task.py`

**Out of scope (do NOT touch):**
- `src/planner/domain/permissions.py` — the gate is already correct; the bug is
  that capture doesn't go through it.
- Any other handler branch (`add_project`, `vacation`, etc.) — already gated.
- The known-sender / actor middleware (a separate, already-planned control).

## Git workflow

- Branch: `advisor/018-capture-task-write-gate`
- Conventional-commit messages (repo style, e.g. `fix(security): gate capture_task behind can_execute`).
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Decide and apply the gating policy (DEFAULT: gate it)

The spec-consistent default is to treat `capture_task` as a write. Add it to
`WRITE_KINDS` and move the capture branch to **after** the `can_execute()`
check so a non-admin is rejected with the existing "Только админ может править
план." message.

1. In `intent.py`, add `"capture_task"` to `WRITE_KINDS`.
2. In `task_router.py::_handle_text`, move the entire
   `if isinstance(intent, CaptureTaskIntent):` block (lines 162-169) to **below**
   the `if not can_execute(...)` block (currently line 171-173), so the gate runs
   first.

**Verify**: `uv run pytest tests/unit/test_permissions.py` → all pass (the gate
is unchanged) and `mypy src/planner --strict` → exit 0.

### Step 2: Validate captured input at the intent boundary

In `intent.py`, constrain `CaptureTaskIntent.task_title` so blank/oversized
titles are rejected by Pydantic before any DB write:

```python
from pydantic import BaseModel, Field   # Field already imported in this module

task_title: str = Field(min_length=1, max_length=200)
project_name: str | None = Field(default=None, max_length=200)
```

In `app/capture_task.py::_resolve_project`, normalize the project name so a
whitespace-only value falls back to `INBOX_PROJECT` instead of creating a blank
stub:

```python
target = (name or "").strip() or INBOX_PROJECT
```

**Verify**: `uv run pytest tests/unit/app/test_capture_task.py` → all pass.

### Step 3: Update the tests that asserted the old (open) behavior

- In `tests/unit/test_intent.py:66`, change the assertion to
  `assert "capture_task" in WRITE_KINDS` and update the comment to reflect the
  admin-only policy.
- In `tests/unit/bot/test_handler_coverage.py`, change
  `test_handle_text_capture_writes_to_db` so the **admin** path writes, and add
  a sibling test asserting a non-admin (`{"is_admin": False}`) gets the
  rejection message and **no** repo write occurs (assert the fake repo's
  `create_task` was not called).

**Verify**: `uv run pytest tests/unit/test_intent.py tests/unit/bot/test_handler_coverage.py` → all pass.

### Step 4: Add input-validation tests

In `tests/unit/app/test_capture_task.py` (model new cases after the existing
happy-path tests there), add cases that a blank/whitespace `task_title` is
rejected (Pydantic `ValidationError` when constructing `CaptureTaskIntent`) and
that a whitespace-only `project_name` resolves to the Inbox project rather than
creating a new stub.

**Verify**: `uv run pytest tests/unit/app/test_capture_task.py` → all pass, new
cases included.

## Test plan

- New/changed tests:
  - `test_intent.py`: `capture_task` is in `WRITE_KINDS`.
  - `test_handler_coverage.py`: admin capture writes; non-admin capture is
    rejected and performs no write.
  - `test_capture_task.py`: blank title rejected; whitespace project name →
    Inbox; oversized title rejected.
- Pattern to follow: the existing fake-repo style already used in
  `tests/unit/app/test_capture_task.py` and `tests/unit/bot/test_handler_coverage.py`.
- Verification: `uv run pytest tests/unit` → all pass.

## Done criteria

ALL must hold:

- [ ] `grep -n "capture_task" src/planner/domain/intent.py` shows it inside `WRITE_KINDS`.
- [ ] In `task_router.py`, the `CaptureTaskIntent` branch appears **after** the `can_execute` check.
- [ ] `uv run pytest tests/unit` → exit 0; a non-admin-capture-rejected test exists and passes.
- [ ] `ruff check .` → exit 0; `mypy src/planner --strict` → exit 0.
- [ ] No files outside the in-scope list modified (`git status`).
- [ ] `plans/README.md` status row updated.

## STOP conditions

Stop and report (do not improvise) if:

- The product intent is that capture is deliberately open to all team members
  (the use-case docstring calls it "the primary low-friction path"). If that is
  the decision, do **not** gate it — instead keep it open, ensure
  `actor_record` is always attributed on the audit row, reconcile the
  `permissions.py` docstring, and report back. The two policies are mutually
  exclusive; the default above is admin-only.
- The capture branch cannot be moved below the gate without breaking the
  `repo is None` degrade-to-echo behavior — report the conflict.
- Any step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- If a future "anyone can capture, only admins can plan" policy is introduced,
  this is the single decision point — revisit `WRITE_KINDS` and the handler
  ordering together.
- Reviewer should confirm the non-admin path performs **zero** repo writes, not
  just returns a message.
- Deferred: rate-limiting of captures and dedup of identical rapid captures are
  out of scope here.
