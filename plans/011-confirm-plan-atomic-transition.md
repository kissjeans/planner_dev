# Plan 011: Make plan confirmation an atomic status transition (eliminate the TOCTOU race)

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

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

Confirming a plan is a read-check-write across three separate DB transactions:
`get_plan_version` (read), `set_plan_version_status` (unconditional write),
`add_audit` (write). Two concurrent confirms — e.g. an admin double-tapping the
inline "✅ Подтвердить" button, which Telegram delivers as two callback
queries — both pass the `status != "proposed"` check and both commit, producing
duplicate `confirm_plan` audit entries and making any future status transition
(e.g. "superseded", needed by plan 012) corruptible. The fix is a single
conditional `UPDATE ... WHERE status = :from` whose rowcount decides success.

## Current state

- `src/planner/app/confirm_plan.py:32-41` — the race:
  ```python
        pv = await self._repo.get_plan_version(plan_version_id)
        if pv is None:
            raise PlanNotFoundError(str(plan_version_id))
        if pv.status != "proposed":
            raise PlanNotProposedError(pv.status)

        await self._repo.set_plan_version_status(plan_version_id, "committed")
        await self._repo.add_audit(
            actor.id, "confirm_plan", "plan_version", plan_version_id, None
        )
  ```
- `src/planner/infra/db/repo.py:75-79` — the unconditional setter:
  ```python
    async def set_plan_version_status(self, pv_id: UUID, status: str) -> None:
        async with self._sf() as s, s.begin():
            pv = await s.get(PlanVersion, pv_id)
            if pv is not None:
                pv.status = status
  ```
- `src/planner/app/ports.py` — `RepoPort` protocol declares
  `set_plan_version_status` near line 96; the new method is declared next to it.
- `tests/unit/app/conftest.py` — `FakeRepo` holds
  `self.plan_versions: dict[UUID, PlanVersionRecord]` and implements
  `set_plan_version_status` by replacing the record (immutable dataclass
  pattern — match it).
- `tests/unit/app/test_confirm_plan.py` — the test pattern: `_proposed(repo)`
  helper, `ADMIN`/`MEMBER` records, async tests without decorators (asyncio
  auto mode).
- SQLAlchemy imports in `repo.py` currently include `func, select` from
  `sqlalchemy` — `update` must be added.

## Commands you will need

| Purpose   | Command                                                                  | Expected on success |
|-----------|--------------------------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/app src/planner/infra/db/repo.py --strict`      | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                                            | exit 0              |
| Tests     | `uv run pytest tests/unit/app/test_confirm_plan.py -v`                   | all pass            |

## Scope

**In scope**:
- `src/planner/infra/db/repo.py` (add `transition_plan_status`)
- `src/planner/app/ports.py` (protocol declaration)
- `src/planner/app/confirm_plan.py` (use the transition)
- `tests/unit/app/conftest.py` (FakeRepo gains the method)
- `tests/unit/app/test_confirm_plan.py`

**Out of scope** (do NOT touch):
- `set_plan_version_status` — leave it in place (other code/tests may use it);
  this plan adds the conditional sibling, it does not delete the old method.
- The bot handler `bot/handlers/confirm.py` — its error handling already maps
  both exceptions to one user message; no change needed.
- Audit semantics — audit is still written only after a successful transition.

## Git workflow

- Branch: `advisor/011-confirm-plan-atomic-transition`
- Commit message: `fix(plan): atomic conditional status transition on confirm`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add `transition_plan_status` to the repo

In `src/planner/infra/db/repo.py`, extend the sqlalchemy import to
`from sqlalchemy import func, select, update` and add below
`set_plan_version_status`:

```python
    async def transition_plan_status(
        self, pv_id: UUID, from_status: str, to_status: str
    ) -> bool:
        """Atomically move a plan version from one status to another.

        Returns True iff exactly this transition happened — a concurrent
        competitor loses because the WHERE clause no longer matches.
        """
        async with self._sf() as s, s.begin():
            result = await s.execute(
                update(PlanVersion)
                .where(PlanVersion.id == pv_id)
                .where(PlanVersion.status == from_status)
                .values(status=to_status)
            )
            return bool(result.rowcount)
```

**Verify**: `uv run mypy src/planner/infra/db/repo.py --strict` → exit 0

### Step 2: Declare it on the RepoPort protocol

In `src/planner/app/ports.py`, next to the existing
`set_plan_version_status` declaration, add:

```python
    async def transition_plan_status(
        self, pv_id: UUID, from_status: str, to_status: str
    ) -> bool: ...
```

**Verify**: `uv run mypy src/planner/app/ports.py --strict` → exit 0

### Step 3: Use it in ConfirmPlanUseCase

In `src/planner/app/confirm_plan.py`, replace the body after the admin check:

```python
        pv = await self._repo.get_plan_version(plan_version_id)
        if pv is None:
            raise PlanNotFoundError(str(plan_version_id))

        moved = await self._repo.transition_plan_status(
            plan_version_id, "proposed", "committed"
        )
        if not moved:
            # Lost the race or never proposed — re-read for the precise status.
            current = await self._repo.get_plan_version(plan_version_id)
            raise PlanNotProposedError(current.status if current else "missing")

        await self._repo.add_audit(
            actor.id, "confirm_plan", "plan_version", plan_version_id, None
        )
        return PlanVersionRecord(
            id=pv.id, project_id=pv.project_id, status="committed", payload=pv.payload
        )
```

**Verify**: `uv run mypy src/planner/app/confirm_plan.py --strict` → exit 0

### Step 4: Teach FakeRepo the transition

In `tests/unit/app/conftest.py`, add to `FakeRepo` (match the immutable-replace
style of `set_plan_version_status`):

```python
    async def transition_plan_status(
        self, pv_id: UUID, from_status: str, to_status: str
    ) -> bool:
        pv = self.plan_versions.get(pv_id)
        if pv is None or pv.status != from_status:
            return False
        self.plan_versions[pv_id] = PlanVersionRecord(
            pv.id, pv.project_id, to_status, pv.payload
        )
        return True
```

**Verify**: `uv run pytest tests/unit/app/test_confirm_plan.py -v` → existing 4 tests pass

### Step 5: Add a double-confirm regression test

In `tests/unit/app/test_confirm_plan.py`:

```python
async def test_double_confirm_second_raises():
    """Two confirms of the same plan: first wins, second gets PlanNotProposedError
    and writes no second audit entry (the TOCTOU regression)."""
    repo = FakeRepo()
    pv = _proposed(repo)
    uc = ConfirmPlanUseCase(repo)
    await uc.execute(pv.id, ADMIN)
    with pytest.raises(PlanNotProposedError):
        await uc.execute(pv.id, ADMIN)
    assert len(repo.audits) == 1
```

**Verify**: `uv run pytest tests/unit/app/test_confirm_plan.py -v` → all pass, including the new test

## Test plan

- New: `test_double_confirm_second_raises` (above) — the regression this plan
  exists for, including the single-audit assertion.
- Regression: the existing 4 tests in `test_confirm_plan.py` must pass
  unchanged — the use-case's external behavior (exceptions, returned record,
  audit on success) is identical for the non-racy paths.
- Note: the true concurrent interleaving is enforced by the DB `WHERE` clause
  and is not unit-testable against `FakeRepo`; the unit test covers the
  sequential double-confirm. A future integration test against real Postgres
  (pattern: `tests/integration/test_repo_full.py`) may add two concurrent
  `transition_plan_status` calls — explicitly out of scope here.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/app src/planner/infra/db/repo.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/app/test_confirm_plan.py -v` exits 0; new double-confirm test passes
- [ ] `grep -n "transition_plan_status" src/planner/infra/db/repo.py src/planner/app/ports.py src/planner/app/confirm_plan.py` shows all three sites
- [ ] `git status --porcelain` lists only the five in-scope files as modified
- [ ] `plans/README.md` status row for 011 updated

## STOP conditions

Stop and report back if:

- `confirm_plan.py` or `repo.py:75` no longer match the excerpts.
- `result.rowcount` is unavailable/None on the SQLAlchemy version in use
  (async `execute` of Core `update` should expose it; if mypy or runtime says
  otherwise, report rather than guessing an alternative).
- Any test outside `test_confirm_plan.py` fails after Step 3 (some other code
  depended on the old non-atomic behavior — report which).

## Maintenance notes

- Plan 012 builds on `transition_plan_status` to supersede stale proposed plans;
  keep the method generic (any from→to pair), not confirm-specific.
- Reviewer focus: the audit entry must be written only when `moved` is True,
  and the "lost the race" path must re-read for an accurate error message.
- `set_plan_version_status` is now a latent footgun next to its safe sibling;
  deleting it is deliberate follow-up scope once no caller remains.
