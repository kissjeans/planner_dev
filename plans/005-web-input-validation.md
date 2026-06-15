# Plan 005: Return 400/422 instead of 500 for malformed web admin input

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
- **Risk**: LOW
- **Depends on**: none (but 007 and 009 also touch `team.py`/`board.py` — see README ordering)
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

Several admin routes pass raw form/query input straight into parsers that raise
`ValueError`, producing unhandled `500` responses with stack traces (QA report
M1, reproduced live):

- bad date in `edit_task` (`date.fromisoformat`)
- bad date in `add_vacation` (`date.fromisoformat`)
- non-UUID in `reassign` (`UUID(...)`)
- negative `offset`/`limit` in `audit` (negative `LIMIT`/`OFFSET` error in Postgres)

A 500 with a stack trace leaks internals and signals an unhandled path. This
plan turns malformed input into a clean `400` (bad value) or `422` (out-of-range
paging), without changing behavior for valid input.

## Current state

- `src/planner/web/app.py` — the app factory already registers exception
  handlers; add one more here:
  ```python
  # app.py:36
      @app.exception_handler(PermissionError)
      async def _forbidden(_request: Request, exc: PermissionError) -> PlainTextResponse:
          return PlainTextResponse(user_message(exc), status_code=403)

      @app.exception_handler(PlanNotFoundError)
      @app.exception_handler(PersonNotFoundError)
      async def _not_found(_request: Request, exc: Exception) -> PlainTextResponse:
          return PlainTextResponse(user_message(exc), status_code=404)
  ```
- `src/planner/web/routes/plan.py:61` — `edit_task` calls
  `date.fromisoformat(start)` / `date.fromisoformat(end)` on form strings.
- `src/planner/web/routes/team.py:58` — `add_vacation` calls
  `date.fromisoformat(day_from)` / `date.fromisoformat(day_to)` on form strings.
- `src/planner/web/routes/board.py:74` — `reassign` calls
  `UUID(task_id)`, `UUID(person_id)` on form strings.
- `src/planner/web/routes/audit.py:17` — query params are unconstrained ints:
  ```python
  @router.get("/audit", response_class=HTMLResponse)
  async def audit_log(
      request: Request,
      limit: int = 50,
      offset: int = 0,
      ...
  ```
- `tests/unit/web/test_web_e2e.py` — web tests use `TestClient` + `WebFakeRepo`.
  The fake repo never touches Postgres, so a `ValueError` raised in a route body
  is what produces the 500; validation tests can assert the new 400/422 against
  the fake. By default `TestClient(raise_server_exceptions=True)` re-raises
  unhandled errors, so today these inputs raise inside the test — the new handler
  converts them to responses.

## Commands you will need

| Purpose   | Command                                              | Expected on success |
|-----------|------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/web --strict`               | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                        | exit 0              |
| Tests     | `uv run pytest tests/unit/web/test_web_e2e.py -v`    | all pass            |

## Scope

**In scope**:
- `src/planner/web/app.py` (add a `ValueError` handler)
- `src/planner/web/routes/audit.py` (constrain paging params)
- `tests/unit/web/test_web_e2e.py`

**Out of scope** (do NOT touch):
- `plan.py`, `team.py`, `board.py` route *bodies* — the global `ValueError`
  handler (Step 1) covers their `date.fromisoformat` / `UUID()` failures without
  per-route try/except. Do not add scattered try/except blocks.
- `team.py`'s `contextlib.suppress(PersonNotFoundError)` — that is plan 007's
  concern; leave it here.
- The response shapes for valid input.

## Git workflow

- Branch: `advisor/005-web-input-validation`
- Commit message: `fix(web): return 400/422 for malformed admin input instead of 500`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add a global ValueError → 400 handler

In `src/planner/web/app.py`, add a handler alongside the existing ones (inside
`create_app`, before `return app`):

```python
    @app.exception_handler(ValueError)
    async def _bad_request(_request: Request, _exc: ValueError) -> PlainTextResponse:
        return PlainTextResponse("Некорректные данные в запросе.", status_code=400)
```

This catches `date.fromisoformat(...)` and `UUID(...)` failures from the form
routes. It does NOT affect Pydantic request validation (those raise
`RequestValidationError`, handled separately by FastAPI as 422).

**Verify**: `uv run mypy src/planner/web/app.py --strict` → exit 0

### Step 2: Constrain audit paging params

In `src/planner/web/routes/audit.py`, import `Query` from fastapi and bound the
params so negatives are rejected as 422 before the repo is called:

```python
from fastapi import APIRouter, Depends, Query, Request
```
```python
async def audit_log(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict[str, Any] = Depends(current_user),
    repo: RepoPort = Depends(get_repo),
) -> HTMLResponse:
```

**Verify**: `uv run mypy src/planner/web/routes/audit.py --strict` → exit 0

### Step 3: Add validation tests

In `tests/unit/web/test_web_e2e.py`, add tests using the existing `client`
fixture and `_auth` helper:

```python
def test_edit_task_bad_date_returns_400(client):
    _auth(client, is_admin=True)
    r = client.post(
        f"/plan/{_PROJECT_ID}/task/{_TASK_ID}/edit",
        data={"start": "not-a-date", "end": ""},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_vacation_bad_date_returns_400(client):
    _auth(client, is_admin=True)
    r = client.post(
        "/team/vacation",
        data={"person_name": "Айгуль", "day_from": "32.13.2026",
              "day_to": "2026-06-11", "capacity_h": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_reassign_non_uuid_returns_400(client):
    _auth(client, is_admin=True)
    r = client.post(
        "/schedule/reassign",
        data={"task_id": "not-a-uuid", "person_id": "also-bad"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_audit_negative_offset_returns_422(client):
    _auth(client)
    assert client.get("/audit?offset=-1").status_code == 422
    assert client.get("/audit?limit=-1").status_code == 422
```

**Verify**: `uv run pytest tests/unit/web/test_web_e2e.py -v` → all pass, including 4 new tests

## Test plan

- New tests in `tests/unit/web/test_web_e2e.py`: bad date in edit (400), bad
  date in vacation (400), non-UUID reassign (400), negative audit paging (422).
- Regression: existing happy-path tests (`test_edit_task_redirects_and_records_update`,
  `test_admin_can_post_vacation`, `test_reassign_admin_moves_task`,
  `test_audit_page_renders`) must still pass — valid input is unaffected.
- Verification: `uv run pytest tests/unit/web/test_web_e2e.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/web --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/web/test_web_e2e.py -v` exits 0; 4 new validation tests pass
- [ ] Bad-date edit, bad-date vacation, non-UUID reassign all return 400 (not 500); negative audit paging returns 422
- [ ] `git status --porcelain` lists only the three in-scope files as modified
- [ ] `plans/README.md` status row for 005 updated

## STOP conditions

Stop and report back if:

- A route in "Current state" no longer performs the `date.fromisoformat` /
  `UUID()` call as shown (someone already added validation — excerpt drifted).
- The `ValueError` handler causes a previously passing test to fail in a way
  that suggests a real `ValueError` was being relied upon as control flow
  elsewhere (report which test).
- FastAPI in this repo does not route `ValueError` to the custom handler
  (e.g. it surfaces as 500 anyway) — report so an alternative (per-route
  validation) can be planned.

## Maintenance notes

- The global `ValueError → 400` handler is deliberately broad. It is safe here
  because the admin routes only raise `ValueError` from input parsing. If future
  routes raise `ValueError` for genuinely internal reasons, prefer raising a
  specific exception type there so it is not masked as a 400.
- A reviewer should confirm valid dates/UUIDs still produce 303 redirects, and
  that the 400 body contains no stack trace or internal detail.
- Follow-up deliberately deferred: structured error pages (currently plain
  text). Out of scope here.
