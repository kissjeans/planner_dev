# Plan 020: the board reassign endpoint validates ids and fetches people once

> **Executor instructions**: Follow step by step; run every verification and
> confirm the expected result before moving on. On a "STOP condition", stop and
> report. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**: working tree was dirty at authoring time. Open
> `src/planner/web/routes/board.py` and confirm the quoted lines match before
> editing. On a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (related to already-DONE plan 005, which validated other
  web inputs but did not cover this newer endpoint)
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-15

## Why this matters

`POST /schedule/reassign` is the only state-changing endpoint on the admin
board. It parses `task_id` and `person_id` straight from form fields with
`UUID(...)`, which raises `ValueError` on any malformed value. There is no
exception handler for `ValueError` in the web app, so a bad form value returns
an HTTP 500 instead of a 4xx — the same class of issue plan 005 fixed for the
other web routes, but this endpoint was added later and was never covered.
Separately, the `GET /schedule` page issues the `list_people()` query twice per
render. Both are cheap to fix and the write path currently has zero error-branch
tests.

## Current state

File: `src/planner/web/routes/board.py`.

```python
# board.py:19-22  — _build_board already fetches people
async def _build_board(repo: RepoPort) -> Board:
    tasks = await repo.list_tasks_with_meta()
    people = await repo.list_people()
    return AdminBoardUseCase().build(tasks=tasks, people=people, start=date.today())

# board.py:25-36  — schedule_page fetches people a SECOND time
@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request, user=Depends(current_user), repo=Depends(get_repo)):
    board = await _build_board(repo)
    people = await repo.list_people()                # <-- duplicate query
    response = request.app.state.templates.TemplateResponse(
        request, "schedule.html", {"board": board, "people": people, "user": user})
    return response

# board.py:65-80  — reassign parses ids unguarded
@router.post("/schedule/reassign")
async def reassign(task_id: str = Form(...), person_id: str = Form(""),
                   user=Depends(require_admin), repo=Depends(get_repo)):
    if not person_id.strip():
        return RedirectResponse("/schedule", status_code=status.HTTP_303_SEE_OTHER)
    tid, pid = UUID(task_id), UUID(person_id)        # <-- ValueError -> 500
    moved = await repo.set_task_assignee(tid, pid)
    if moved:
        await repo.add_audit(None, "reassign_task", "task", tid, {"person_id": person_id})
    return RedirectResponse("/schedule", status_code=status.HTTP_303_SEE_OTHER)
```

Exception handlers registered in `src/planner/web/app.py:36-47` cover
`PermissionError`, `PlanNotFound`, `PersonNotFound`, `PlanNotProposed` — **not**
`ValueError`. So the bad-id path is uncaught → 500.

`RepoPort.set_task_assignee(task_id, person_id, hours=8) -> bool` returns `False`
when the task does not exist (`app/ports.py:153-157`).

Existing reassign tests: `tests/unit/web/test_web_e2e.py:222-253` cover the
admin-moves-task, empty-person no-op, and member-blocked (403) cases — none
exercise a malformed UUID or an unknown task id.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Tests   | `uv run pytest tests/unit/web/test_web_e2e.py` | all pass |
| Types   | `mypy src/planner --strict` | exit 0 |
| Lint    | `ruff check .` | exit 0 |

## Scope

**In scope:**
- `src/planner/web/routes/board.py`
- `tests/unit/web/test_web_e2e.py`

**Out of scope:**
- `src/planner/web/app.py` — prefer validating in the route (below) over adding
  a global `ValueError` handler, which would mask genuine bugs elsewhere. Do not
  edit it.
- `src/planner/app/admin_board.py` — covered by plan 019.
- The audit `actor_id=None` argument — recording the real actor here is the same
  concern as already-DONE plan 009; leave the `None` as-is for now so this plan
  stays surgical (note it in Maintenance).

## Git workflow

- Branch: `advisor/020-board-route-hardening`
- Conventional commits (e.g. `fix(web): return 400 on malformed reassign ids; drop duplicate people query`).
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Validate the ids in `reassign`

Parse the UUIDs defensively and return an HTTP 400 (or redirect to `/schedule`
with no write) on a malformed value, instead of letting `ValueError` escape:

```python
from uuid import UUID
from fastapi import HTTPException

try:
    tid, pid = UUID(task_id), UUID(person_id)
except ValueError as exc:
    raise HTTPException(status_code=400, detail="bad id") from exc
```

Keep the existing empty-person no-op redirect ahead of this. The unknown-task
case is already handled: `set_task_assignee` returns `False` and the code skips
the audit row, then redirects — that is acceptable (no 500, no bogus audit).

**Verify**: `uv run pytest tests/unit/web/test_web_e2e.py` → existing reassign
tests still pass.

### Step 2: Fetch people once in `schedule_page`

Eliminate the duplicate `list_people()`. The simplest surgical change: have
`_build_board` return the people it already fetched, or fetch people once in
`schedule_page` and pass the same list into both the board build and the
template. Pick the smaller diff. If you change `_build_board`'s return type,
update its other caller(s) — `calendar_page` and `load_board_page` also call
`_build_board` (board.py:45, 58); keep them working.

**Verify**: `mypy src/planner --strict` → exit 0; `uv run pytest tests/unit/web/test_web_e2e.py` → all pass.

### Step 3: Add error-path tests

In `tests/unit/web/test_web_e2e.py` (model after the reassign tests at lines
222-253), add:
1. POST `/schedule/reassign` with a non-UUID `task_id` → assert status is 400
   (not 500) and the fake repo's `set_task_assignee` was **not** called.
2. POST with a valid-but-unknown `task_id` (fake repo `set_task_assignee`
   returns `False`) → assert a 303 redirect and that **no** audit row was added.

**Verify**: `uv run pytest tests/unit/web/test_web_e2e.py` → all pass, new cases included.

## Test plan

- New tests in `tests/unit/web/test_web_e2e.py`, following the existing
  TestClient + fake-repo pattern used by the reassign tests there.
- Verification: `uv run pytest tests/unit/web` → all pass.

## Done criteria

ALL must hold:

- [ ] `grep -n "list_people" src/planner/web/routes/board.py` shows the query is not issued twice for one `/schedule` render.
- [ ] A malformed reassign id returns 400 (test asserts it) — not 500.
- [ ] `uv run pytest tests/unit/web/test_web_e2e.py` → exit 0 with the 2 new cases.
- [ ] `ruff check .` → exit 0; `mypy src/planner --strict` → exit 0.
- [ ] No files outside the in-scope list modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

- Changing `_build_board`'s signature breaks `calendar_page`/`load_board_page`
  in a way you cannot resolve surgically — fall back to fetching people once
  inside `schedule_page` only, and report.
- A test reveals the global app already maps `ValueError` to a 4xx (i.e. the
  500 does not reproduce) — then Step 1 is unnecessary; report and keep only
  Steps 2–3.

## Maintenance notes

- The audit call still passes `actor_id=None` (board.py:78). Recording the real
  admin actor is the concern of already-DONE plan 009; when that pattern is
  applied repo-wide, extend it to this endpoint too.
- Reviewer: confirm the unknown-task path still writes no audit row.
