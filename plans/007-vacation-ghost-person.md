# Plan 007: Surface an error when vacation is set for an unknown person (stop the silent success)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Instead, open the files in "Current state" and confirm the
> quoted excerpts match the live code. On any mismatch, treat it as a STOP
> condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 005 (both edit `team.py add_vacation`; execute 005 first)
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

Posting a vacation for a person who is not in the team returns a `303` redirect
that looks like success, but nothing is written (QA report M2). The admin
believes a day-off was recorded when it was not. The cause is a
`contextlib.suppress(PersonNotFoundError)` wrapping the use-case call. The app
already has a registered handler that maps `PersonNotFoundError` → `404` with a
clear message ("Не нашёл такого человека в команде."), so the fix is simply to
stop swallowing the error and let that handler respond.

## Current state

- `src/planner/web/routes/team.py` — the silent-suppress:
  ```python
  # team.py:47
  @router.post("/team/vacation")
  async def add_vacation(
      person_name: str = Form(...),
      day_from: str = Form(...),
      day_to: str = Form(...),
      capacity_h: int = Form(0),
      user: dict[str, Any] = Depends(require_admin),
      repo: RepoPort = Depends(get_repo),
  ) -> RedirectResponse:
      intent = VacationIntent(
          person_name=person_name,
          day_from=date.fromisoformat(day_from),
          day_to=date.fromisoformat(day_to),
          capacity_h=capacity_h,
      )
      import contextlib
      with contextlib.suppress(PersonNotFoundError):
          await SetVacationUseCase(repo).execute(intent, _actor(user))
      return RedirectResponse("/team", status_code=status.HTTP_303_SEE_OTHER)
  ```
- `src/planner/web/app.py:40` — `PersonNotFoundError` is already mapped to 404:
  ```python
      @app.exception_handler(PlanNotFoundError)
      @app.exception_handler(PersonNotFoundError)
      async def _not_found(_request: Request, exc: Exception) -> PlainTextResponse:
          return PlainTextResponse(user_message(exc), status_code=404)
  ```
- `src/planner/app/set_vacation.py:28` — raises `PersonNotFoundError` when the
  person name does not resolve.
- `tests/unit/web/test_web_e2e.py:287` — this test pins the CURRENT (wrong)
  behavior and MUST be updated:
  ```python
  def test_vacation_unknown_person_still_redirects(client):
      """PersonNotFoundError is suppressed — redirect happens anyway."""
      _auth(client, is_admin=True)
      r = client.post(
          "/team/vacation",
          data={"person_name": "Призрак", "day_from": "2026-06-10",
                "day_to": "2026-06-10", "capacity_h": "0"},
          follow_redirects=False,
      )
      assert r.status_code == 303
  ```
  The fake repo's `get_person_by_name` returns `None` for "Призрак", so the
  use-case raises `PersonNotFoundError`.

## Commands you will need

| Purpose   | Command                                              | Expected on success |
|-----------|------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/web/routes/team.py --strict`| exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                        | exit 0              |
| Tests     | `uv run pytest tests/unit/web/test_web_e2e.py -v`    | all pass            |

## Scope

**In scope**:
- `src/planner/web/routes/team.py` (`add_vacation` only)
- `tests/unit/web/test_web_e2e.py`

**Out of scope** (do NOT touch):
- `src/planner/web/app.py` — the 404 handler already exists; reuse it, do not
  add another.
- `src/planner/app/set_vacation.py` — it raises the right exception already.
- `_actor()` in `team.py` — that is plan 009's concern.
- The `date.fromisoformat` calls — plan 005 handles malformed dates.

## Git workflow

- Branch: `advisor/007-vacation-ghost-person`
- Commit message: `fix(web): surface 404 when setting vacation for unknown person`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Remove the suppress so the error reaches the 404 handler

In `src/planner/web/routes/team.py`, replace the suppressed call with a plain
call (delete the `import contextlib` and the `with` block):

```python
    await SetVacationUseCase(repo).execute(intent, _actor(user))
    return RedirectResponse("/team", status_code=status.HTTP_303_SEE_OTHER)
```

Also remove the now-unused import of `PersonNotFoundError` from the top of the
file **only if** it is no longer referenced anywhere else in `team.py` (check
with the grep in Done criteria). If it is still imported but unused, ruff will
flag it.

**Verify**: `uv run ruff check src/planner/web/routes/team.py` → exit 0 (no unused-import warning)

### Step 2: Update the test to expect 404, add a happy-path assertion

In `tests/unit/web/test_web_e2e.py`, replace
`test_vacation_unknown_person_still_redirects` with:

```python
def test_vacation_unknown_person_returns_404(client):
    """Setting vacation for someone not in the team is an error, not a silent ok."""
    _auth(client, is_admin=True)
    r = client.post(
        "/team/vacation",
        data={"person_name": "Призрак", "day_from": "2026-06-10",
              "day_to": "2026-06-10", "capacity_h": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 404
```

The existing `test_admin_can_post_vacation` (known person "Айгуль" → 303 + 2
overrides written) already covers the happy path; confirm it still passes.

**Verify**: `uv run pytest tests/unit/web/test_web_e2e.py -v` → all pass

## Test plan

- Update: `test_vacation_unknown_person_still_redirects` →
  `test_vacation_unknown_person_returns_404` (now 404, not 303).
- Regression: `test_admin_can_post_vacation` still passes (known person writes 2
  overrides, 303).
- Verification: `uv run pytest tests/unit/web/test_web_e2e.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/web/routes/team.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0 (no unused-import warning for `PersonNotFoundError`)
- [ ] `uv run pytest tests/unit/web/test_web_e2e.py -v` exits 0
- [ ] `grep -n "contextlib" src/planner/web/routes/team.py` returns nothing
- [ ] Unknown-person vacation returns 404; known-person vacation still returns 303 and writes overrides
- [ ] `git status --porcelain` lists only the two in-scope files as modified
- [ ] `plans/README.md` status row for 007 updated

## STOP conditions

Stop and report back if:

- `add_vacation` no longer matches the "Current state" excerpt (e.g. plan 005
  changed it — re-read and adapt; if the suppress is already gone, mark this
  plan DONE/REJECTED with a note).
- Removing the suppress causes a 500 instead of 404 (would mean the
  `PersonNotFoundError` handler is no longer registered — report it).
- `PersonNotFoundError` is imported but used elsewhere in `team.py` and you are
  unsure whether to remove the import — leave the import and report.

## Maintenance notes

- This is a one-line behavior change with a big correctness payoff: failed
  writes now tell the operator. Watch for any UI/template that assumed the
  redirect always happens.
- A reviewer should confirm the 404 body uses the friendly message from
  `app/errors.py` and leaks no internals.
- Note for ordering: plan 005 (input validation) also edits `add_vacation`. If
  005 already landed, the `date.fromisoformat` lines may now be reached only for
  valid dates — that does not conflict with this change.
