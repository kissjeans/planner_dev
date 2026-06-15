# Plan 014: Solve before persisting — no orphan project rows when AddProject fails

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

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (plan 016 builds on this — execute 014 before 016)
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

`AddProjectUseCase.execute` runs three separate DB transactions with the solver
in between: `create_project` (txn 1) → `solver.plan` → `save_plan_version`
(txn 2) → `add_audit` (txn 3). If the solver raises — concretely, a cyclic
dependency in a template makes `nx.topological_sort` raise
`NetworkXUnfeasible` (`domain/solver/greedy.py:186`) — the project row from
txn 1 is already committed: an orphan "planning" project with no plan and no
tasks, surfaced to users in `/plan` lists. The fix: do all pure computation
(instantiate + solve) BEFORE the first write, and convert the cycle error into
the existing `InvalidProjectError` so the bot answers with a friendly message
instead of the generic error boundary.

Residual risk accepted: if `save_plan_version` itself fails after
`create_project` succeeds, an orphan can still occur. That window no longer
contains the solver (the realistic failure source) and is documented below.

## Current state

- `src/planner/app/add_project.py:196-246` — current write-then-solve order:
  ```python
        project = await self._repo.create_project(
            title=title, template_code=intent.template_code,
            deadline=intent.deadline, brief_return_date=intent.brief_return_date,
            actor_id=actor.id,
        )

        tasks, deps = instantiate_template(template, project.id)
        req = PlanRequest(...)
        plan = self._solver.plan(req)
        earliest_end = (... critical_path_end ...) if intent.deadline is None else None

        pv = await self._repo.save_plan_version(
            project.id, "proposed", serialize_plan(plan), actor.id
        )
        await self._repo.add_audit(...)
  ```
- `instantiate_template(template, project_id)` is pure — it only needs a UUID,
  not a persisted row (`add_project.py:65-97`). The solver never reads
  `task.project_id`.
- `src/planner/infra/db/repo.py:97-127` — `create_project` generates its id
  internally (`project_id = uuid4()` at line 107). To create the row *after*
  solving with tasks already bound to the project id, the method gains an
  optional `project_id` parameter.
- `src/planner/app/ports.py:108-117` — `RepoPort.create_project` declaration
  (keyword-only params: title, template_code, deadline, brief_return_date,
  actor_id).
- Fakes implementing `create_project` (both must gain the optional param):
  - `tests/unit/app/conftest.py:25-30` (`FakeRepo.create_project`, generates
    `uuid4()` internally)
  - `tests/unit/bot/test_handler_coverage.py:88-91` (`_FakeRepo.create_project`)
- `tests/unit/app/test_add_project.py` — the use-case's test file; pattern
  source for the new cycle test (uses `FakeRepo`, `GreedySolver(WeekendCalendar())`,
  `ProjectTemplate`/`TemplateTaskSpec`).
- `domain/solver/greedy.py:184-186`:
  ```python
        graph = build_dag(list(req.tasks), list(req.dependencies))
        # Raises networkx.NetworkXUnfeasible on a cycle (spec acceptance).
        order = list(nx.topological_sort(graph))
  ```

## Commands you will need

| Purpose   | Command                                                                       | Expected on success |
|-----------|--------------------------------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/app src/planner/infra/db/repo.py --strict`            | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                                                  | exit 0              |
| Tests     | `uv run pytest tests/unit/app/test_add_project.py tests/unit/bot/test_handler_coverage.py -v` | all pass |

## Scope

**In scope**:
- `src/planner/app/add_project.py` (reorder `execute`, catch the cycle error)
- `src/planner/infra/db/repo.py` (`create_project` optional id)
- `src/planner/app/ports.py` (protocol signature)
- `tests/unit/app/conftest.py`, `tests/unit/bot/test_handler_coverage.py` (fakes)
- `tests/unit/app/test_add_project.py` (cycle test)

**Out of scope** (do NOT touch):
- The solver — raising on a cycle is spec behavior.
- `capture_task.py` — its `create_project` call keeps not passing an id
  (the param is optional precisely so existing callers are untouched).
- Transactional unification of `save_plan_version` + `add_audit` (single-txn
  repo composition) — bigger refactor, deliberately deferred.

## Git workflow

- Branch: `advisor/014-add-project-atomicity`
- Commit message: `fix(plan): solve before persisting; friendly error on cyclic template`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: `create_project` accepts an optional pre-generated id

`src/planner/infra/db/repo.py` — add the keyword param and use it:

```python
    async def create_project(
        self,
        *,
        title: str,
        template_code: str,
        deadline: date | None,
        brief_return_date: date | None,
        actor_id: UUID | None,
        priority: str = "medium",
        project_id: UUID | None = None,
    ) -> ProjectRecord:
        project_id = project_id or uuid4()
        async with self._sf() as s, s.begin():
            ...
```

(The rest of the method body is unchanged — it already uses the local
`project_id` variable.)

`src/planner/app/ports.py` — mirror the addition on the protocol:

```python
        project_id: UUID | None = None,
```

**Verify**: `uv run mypy src/planner/infra/db/repo.py src/planner/app/ports.py --strict` → exit 0

### Step 2: Update both fakes

`tests/unit/app/conftest.py`:

```python
    async def create_project(
        self, *, title, template_code, deadline, brief_return_date, actor_id,
        project_id=None,
    ) -> ProjectRecord:
        rec = ProjectRecord(project_id or uuid4(), title, "planning", deadline)
        self.projects[rec.id] = rec
        return rec
```

`tests/unit/bot/test_handler_coverage.py` (`_FakeRepo`): add the same
`project_id=None` keyword and pass it through to the returned `ProjectRecord`.

**Verify**: `uv run pytest tests/unit/app tests/unit/bot/test_handler_coverage.py -v` → all pass (no behavior change yet)

### Step 3: Reorder execute() — pure work first, writes last

In `src/planner/app/add_project.py`, replace the body of `execute` after the
three validation checks with:

```python
        from uuid import uuid4 as _uuid4  # if uuid4 is not already imported at top

        project_id = uuid4()
        tasks, deps = instantiate_template(template, project_id)
        req = PlanRequest(
            people=people,
            tasks=tasks,
            dependencies=deps,
            horizon_start=today,
            day_overrides=day_overrides,
            existing_allocations=existing_allocations,
            deadline=intent.deadline,
        )

        try:
            plan = self._solver.plan(req)
        except nx.NetworkXUnfeasible as exc:
            raise InvalidProjectError("Цикл в зависимостях шаблона.") from exc
        earliest_end = (
            self._solver.critical_path_end(req, today)
            if intent.deadline is None
            else None
        )

        project = await self._repo.create_project(
            title=title,
            template_code=intent.template_code,
            deadline=intent.deadline,
            brief_return_date=intent.brief_return_date,
            actor_id=actor.id,
            project_id=project_id,
        )

        pv = await self._repo.save_plan_version(
            project.id, "proposed", serialize_plan(plan), actor.id
        )
        await self._repo.add_audit(
            actor.id, "add_project", "project", project.id, {"title": title}
        )
```

Imports: `uuid4` is already imported in this module (`from uuid import UUID,
uuid4`); add `import networkx as nx` to the imports (the domain layer already
depends on networkx, so the app layer referencing the exception type does not
add a new dependency).

**Verify**: `uv run mypy src/planner/app/add_project.py --strict` → exit 0

### Step 4: Cycle regression test

In `tests/unit/app/test_add_project.py`, model on the file's existing tests
(read it first for the exact fixture style) and add:

```python
async def test_cyclic_template_raises_invalid_and_writes_nothing():
    """A template whose deps form a cycle must fail BEFORE any DB write."""
    from planner.domain.calendar.rules import WeekendCalendar
    from planner.domain.solver.greedy import GreedySolver

    person = Person(id=uuid4(), name="Андрей", capacity_h=8)
    template = ProjectTemplate(
        code="standard",
        tasks=(
            TemplateTaskSpec(1, "A", 8, (person.id,), depends_on_ords=(2,)),
            TemplateTaskSpec(2, "B", 8, (person.id,), depends_on_ords=(1,)),
        ),
    )
    repo = FakeRepo()
    intent = AddProjectIntent(title="Цикл", template_code="standard")
    with pytest.raises(InvalidProjectError):
        await AddProjectUseCase(repo, GreedySolver(WeekendCalendar())).execute(
            intent, PersonRecord(id=uuid4(), name="Менеджер", is_admin=True),
            (person,), template, today=date.today(),
        )
    assert repo.projects == {}        # no orphan project row
    assert repo.plan_versions == {}   # no plan version
    assert repo.audits == []          # no audit entry
```

Adjust imports/names to the file's existing header (it already imports most of
these for its other tests).

**Verify**: `uv run pytest tests/unit/app/test_add_project.py -v` → all pass, including the new test

## Test plan

- New: `test_cyclic_template_raises_invalid_and_writes_nothing` — cycle →
  `InvalidProjectError`, zero writes (the orphan-row regression).
- Regression: all existing `test_add_project.py` tests (happy path, validation
  errors, backward mode) and
  `test_handle_text_add_project_with_repo_sends_keyboard` in handler coverage —
  the result shape and reply text are unchanged.
- Verification: `uv run pytest tests/unit/app/test_add_project.py tests/unit/bot/test_handler_coverage.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/app src/planner/infra/db/repo.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/app tests/unit/bot/test_handler_coverage.py -v` exits 0
- [ ] In `add_project.py`, `self._solver.plan(` appears textually BEFORE `self._repo.create_project(` inside `execute`
- [ ] New test proves a cyclic template writes nothing
- [ ] `git status --porcelain` lists only the six in-scope files as modified
- [ ] `plans/README.md` status row for 014 updated

## STOP conditions

Stop and report back if:

- `execute` no longer matches the "Current state" excerpt.
- The bot reply for an invalid project no longer routes through
  `InvalidProjectError` (`task_router.py::build_add_project_reply` catches it
  and answers "Не могу создать проект: …" — if that catch is gone, report).
- Some caller of `create_project` passes positional arguments (all calls should
  be keyword-only; if not, report instead of reordering parameters).
- `test_add_project.py`'s fixture style differs so much that the test above
  cannot be adapted mechanically — report with the file's actual pattern.

## Maintenance notes

- Plan 016 (persist template tasks) inserts task rows in this same `execute`
  flow — it assumes this plan's compute-first ordering. Execute 014 → 016.
- Residual non-atomicity: `create_project` → `save_plan_version` → `add_audit`
  are still three transactions; a mid-sequence crash can orphan a project
  without a plan. Full fix is a repo-level composed transaction
  (`create_project_with_plan(...)`) — deferred, noted in the index.
- Reviewer focus: the pre-generated `project_id` must be the one passed to
  `create_project`, and `tasks` must be bound to that same id (otherwise plan
  016's task rows would point at a phantom project).
