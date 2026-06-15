# Plan 009: Record the real actor on audit entries (fix broken attribution)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Instead, open each file in "Current state" and confirm the
> quoted excerpts match the live code. On any mismatch, treat it as a STOP
> condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 005 (both touch `board.py`); also coordinate with 007 (`team.py`)
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

The audit log is supposed to record *who* did *what* (spec §17). Today the
attribution is wrong in two ways (QA report L5):

1. `reassign` and `edit_task` pass `actor_id=None` to `add_audit`, so those
   actions have no actor recorded at all.
2. `team.py:_actor()` mints a **random** `uuid4()` for any non-UUID JWT `sub`
   (e.g. the dev-login `sub="dev"`) and hardcodes `is_admin=True`. The random id
   pollutes vacation audit rows with a person id that does not exist.

This plan derives the actor id from the JWT `sub` (which is the real person UUID
for normal logins) and threads it into the audit calls; when `sub` is not a real
person UUID it records `None` (honestly "unknown") instead of a fake id.

## Current state

- `src/planner/web/deps.py` — auth dependencies live here; this is where the
  shared helper belongs:
  ```python
  def current_user(request: Request, secret: str = Depends(get_jwt_secret)) -> dict[str, Any]:
      ...
  def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
      ...
  ```
- `src/planner/web/routes/team.py:20` — `_actor()` mints a random id and forces
  admin:
  ```python
  def _actor(user: dict[str, Any]) -> PersonRecord:
      sub = user.get("sub", "")
      try:
          pid = UUID(sub)
      except (ValueError, TypeError):
          pid = uuid4()
      return PersonRecord(id=pid, name=user.get("name", "—"), is_admin=True)
  ```
- `src/planner/web/routes/board.py:76` — reassign audits with `None`:
  ```python
      moved = await repo.set_task_assignee(tid, pid)
      if moved:
          await repo.add_audit(
              None, "reassign_task", "task", tid, {"person_id": person_id}
          )
  ```
- `src/planner/web/routes/plan.py:67` — edit_task audits with `None`:
  ```python
      await repo.add_audit(
          None, "edit_task", "task", task_id, {"start": start, "end": end}
      )
  ```
- `RepoPort.add_audit` signature (`app/ports.py`): first arg
  `actor_id: UUID | None`. So passing a real `UUID | None` is type-correct.
- `tests/unit/web/test_web_e2e.py` — `WebFakeRepo.add_audit` currently records
  only `(action, entity_type)`:
  ```python
      async def add_audit(self, actor_id, action, entity_type, entity_id, payload):
          self.audits.append((action, entity_type))
  ```
  No existing test indexes `self.audits` by element, but **verify this** before
  changing the tuple (see Step 4).

## Commands you will need

| Purpose   | Command                                              | Expected on success |
|-----------|------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/web --strict`               | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                        | exit 0              |
| Tests     | `uv run pytest tests/unit/web/test_web_e2e.py -v`    | all pass            |

## Scope

**In scope**:
- `src/planner/web/deps.py` (add `actor_id_from` helper)
- `src/planner/web/routes/board.py` (reassign audit call)
- `src/planner/web/routes/plan.py` (edit_task audit call)
- `src/planner/web/routes/team.py` (`_actor` is_admin honesty)
- `tests/unit/web/test_web_e2e.py`

**Out of scope** (do NOT touch):
- `SetVacationUseCase` and other use-cases — they already take an actor.
- The DB schema / `AuditLog` model — `actor_id` is already nullable.
- Login routes — the `sub` claim is set correctly there.

## Git workflow

- Branch: `advisor/009-audit-attribution`
- Commit message: `fix(audit): record real actor id on reassign/edit/vacation`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add an actor-id helper in deps.py

In `src/planner/web/deps.py`, add (import `UUID` from `uuid` at the top):

```python
from uuid import UUID
```
```python
def actor_id_from(user: dict[str, Any]) -> UUID | None:
    """Return the actor's person UUID from the JWT ``sub``, or None when the
    subject is not a real person id (e.g. the dev-login ``sub='dev'`` or a
    ``tg:<id>`` subject for a user not yet in the team)."""
    try:
        return UUID(str(user.get("sub", "")))
    except (ValueError, TypeError):
        return None
```

**Verify**: `uv run mypy src/planner/web/deps.py --strict` → exit 0

### Step 2: Use the helper in board.py reassign

In `src/planner/web/routes/board.py`, import the helper and replace the `None`
actor id:

```python
from planner.web.deps import actor_id_from, current_user, get_repo, require_admin
```
```python
        await repo.add_audit(
            actor_id_from(user), "reassign_task", "task", tid, {"person_id": person_id}
        )
```

**Verify**: `uv run mypy src/planner/web/routes/board.py --strict` → exit 0

### Step 3: Use the helper in plan.py edit_task

In `src/planner/web/routes/plan.py`, import the helper and replace the `None`
actor id:

```python
from planner.web.deps import actor_id_from, current_user, get_repo, require_admin
```
```python
    await repo.add_audit(
        actor_id_from(user), "edit_task", "task", task_id, {"start": start, "end": end}
    )
```

**Verify**: `uv run mypy src/planner/web/routes/plan.py --strict` → exit 0

### Step 4: Make _actor() honest about admin, and capture actor_id in the fake repo

In `src/planner/web/routes/team.py`, change `_actor` so `is_admin` reflects the
claim rather than being hardcoded `True` (the route is already `require_admin`,
so behavior is unchanged, but the value is now truthful):

```python
def _actor(user: dict[str, Any]) -> PersonRecord:
    sub = user.get("sub", "")
    try:
        pid = UUID(sub)
    except (ValueError, TypeError):
        pid = uuid4()
    return PersonRecord(
        id=pid, name=user.get("name", "—"), is_admin=bool(user.get("is_admin", False))
    )
```

Then, in `tests/unit/web/test_web_e2e.py`, first confirm nothing reads the old
audit tuple shape:

```
grep -n "\.audits" tests/unit/web/test_web_e2e.py
```

If the only references are membership/length checks (not index `[0]`/`[1]` on
the tuple), update `WebFakeRepo.add_audit` to record the actor id too:

```python
    async def add_audit(self, actor_id, action, entity_type, entity_id, payload):
        self.audits.append((actor_id, action, entity_type))
```

If any test indexes the old 2-tuple, STOP and report.

**Verify**: `uv run pytest tests/unit/web/test_web_e2e.py -v` → all pass

### Step 5: Add an attribution test

In `tests/unit/web/test_web_e2e.py`, add:

```python
def test_reassign_records_actor_id(client):
    from uuid import UUID
    sub = str(uuid4())
    token = create_jwt({"sub": sub, "name": "Admin", "is_admin": True}, JWT_SECRET)
    client.cookies.set(COOKIE_NAME, token)
    pid = client.repo.people["Айгуль"].id  # type: ignore[attr-defined]
    r = client.post(
        "/schedule/reassign",
        data={"task_id": str(_TASK_ID), "person_id": str(pid)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    recorded_actor = client.repo.audits[0][0]  # type: ignore[attr-defined]
    assert recorded_actor == UUID(sub)
```

**Verify**: `uv run pytest tests/unit/web/test_web_e2e.py::test_reassign_records_actor_id -v` → passes

## Test plan

- New: `test_reassign_records_actor_id` — the JWT `sub` UUID is what lands in the
  audit row (not `None`, not a random id).
- Update: `WebFakeRepo.add_audit` records `actor_id` (only if no test indexes the
  old tuple shape).
- Regression: `test_reassign_admin_moves_task`, `test_admin_can_post_vacation`,
  `test_edit_task_redirects_and_records_update` still pass.
- Verification: `uv run pytest tests/unit/web/test_web_e2e.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/web --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/web/test_web_e2e.py -v` exits 0
- [ ] `grep -rn "add_audit(\s*None" src/planner/web/routes/` returns nothing (no hardcoded None actors)
- [ ] A test proves the reassign audit row carries the JWT `sub` UUID
- [ ] `git status --porcelain` lists only the five in-scope files as modified
- [ ] `plans/README.md` status row for 009 updated

## STOP conditions

Stop and report back if:

- A route's `add_audit` call no longer matches the "Current state" excerpt.
- A test indexes `WebFakeRepo.audits` tuples by position (changing the shape
  would break it) — report so the shape change can be reconsidered.
- `PersonRecord` no longer accepts the fields shown (schema drifted).

## Maintenance notes

- The dev-login path (`sub="dev"`) still produces a `uuid4()` person id inside
  `_actor()` for vacation, because `PersonRecord.id` is non-optional. That edge
  is acceptable and shrinking once plan 002 locks `/dev-login` to loopback; if
  `PersonRecord.id` is ever made optional, revisit `_actor` to record `None`.
- A reviewer should confirm `actor_id_from` returns `None` (not a random id) for
  non-UUID subjects, and that real logins (sub = person UUID) attribute
  correctly.
- Ordering: plans 005 and 007 also touch `board.py`/`team.py`. Execute in
  numeric order; the drift check protects you.
