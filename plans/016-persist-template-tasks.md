# Plan 016: Persist template-project tasks to the tasks table so web pages can see them

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
- **Depends on**: plans/014-add-project-atomicity.md (compute-first ordering in `execute`)
- **Category**: bug / architecture
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

A project created from a template via the bot stores its tasks **only inside
the plan-version JSON payload**. No rows are written to the `tasks` table —
`repo.create_task` has exactly one caller, the capture-task flow (grep-proven).
But the web admin reads the `tasks` table everywhere: `/plan/{project_id}`
detail uses `list_project_tasks`, and the Schedule/Calendar/Load boards use
`list_tasks_with_meta`. Result: every bot-created template project renders an
**empty plan-detail page and is invisible on the boards**, while captured
ad-hoc tasks show up fine. This plan persists the instantiated tasks (with
their solver-assigned dates and assignees) in one repo call inside
`AddProjectUseCase`.

**Recorded design decision** (this plan implements it): the `tasks` table is
the source of truth for web display; the plan payload remains the source for
capacity math (`list_committed_plans` → `deserialize_allocations`). Boards
showing template tasks alongside captured tasks is the intended outcome, not
double-counting — capacity calculations do not read the tasks table.

## Current state

- Sole `create_task` caller (grep `create_task` in `src/`):
  `src/planner/app/capture_task.py:51`.
- `src/planner/app/add_project.py` (post-plan-014 shape): `execute` computes
  `tasks, deps = instantiate_template(template, project_id)`, solves to get
  `plan: PlanResult`, then persists project → plan version → audit. The solver
  result maps task → assignment:
  - `PlanResult.assignments: tuple[Assignment, ...]` where domain `Assignment`
    has `task_id, person_id, start_date, end_date, allocations`
    (see `domain/models.py`).
  - Domain `Task` has `id, name, duration_hours, allowed_person_ids,
    project_id, is_splittable, allow_two_assignees` (see `domain/models.py`).
- `src/planner/infra/db/models.py` — ORM `Task` columns used by the existing
  `create_task` (repo.py:160-170): `id, project_id, name, duration_hours,
  end_date, status` (+ `start_date`, used by `update_task_schedule`,
  repo.py:216-219). ORM `Assignment` is keyed by `(task_id, person_id)` with an
  `hours` column (see `assign_task`, repo.py:179-187).
- `src/planner/infra/db/repo.py` — transaction idiom to copy:
  `async with self._sf() as s, s.begin():` then `s.add(...)` per row.
- `src/planner/app/ports.py` — `RepoPort` protocol; new method declared here.
- Fakes needing the new method as a recording no-op:
  `tests/unit/app/conftest.py::FakeRepo`,
  `tests/unit/bot/test_handler_coverage.py::_FakeRepo`.
- `tests/unit/app/test_add_project.py` — test pattern source.

## Commands you will need

| Purpose   | Command                                                                        | Expected on success |
|-----------|----------------------------------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/app src/planner/infra/db/repo.py --strict`              | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                                                    | exit 0              |
| Tests     | `uv run pytest tests/unit/app/test_add_project.py tests/unit/bot/test_handler_coverage.py -v` | all pass |

## Scope

**In scope**:
- `src/planner/infra/db/repo.py` (new `save_project_tasks`)
- `src/planner/app/ports.py` (protocol)
- `src/planner/app/add_project.py` (call it in `execute`)
- `tests/unit/app/conftest.py`, `tests/unit/bot/test_handler_coverage.py` (fakes)
- `tests/unit/app/test_add_project.py`

**Out of scope** (do NOT touch):
- `capture_task.py` / `create_task` — the capture path is already correct.
- Capacity/load code (`list_committed_plans`, `deserialize_allocations`,
  `/load` rendering) — payload stays its source of truth.
- Schema/migrations — all needed columns exist.
- Web routes/templates — they already read the tasks table; they start showing
  data without changes.

## Git workflow

- Branch: `advisor/016-persist-template-tasks`
- Commit message: `feat(plan): persist instantiated template tasks + assignees to tasks table`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Repo method — bulk insert tasks + assignments in ONE transaction

In `src/planner/infra/db/repo.py` (domain types `Task as DomainTask`,
`Assignment as DomainAssignment` — extend the existing
`from planner.domain.models import Person as DomainPerson` import):

```python
    async def save_project_tasks(
        self,
        project_id: UUID,
        tasks: tuple[DomainTask, ...],
        assignments: tuple[DomainAssignment, ...],
    ) -> None:
        """Persist instantiated template tasks with their planned schedule.

        One transaction: either the whole task set lands or none of it.
        """
        by_task = {a.task_id: a for a in assignments}
        async with self._sf() as s, s.begin():
            for t in tasks:
                a = by_task.get(t.id)
                s.add(
                    Task(
                        id=t.id,
                        project_id=project_id,
                        name=t.name,
                        duration_hours=t.duration_hours,
                        start_date=a.start_date if a else None,
                        end_date=a.end_date if a else None,
                        status="not_done",
                    )
                )
                if a is not None:
                    s.add(
                        Assignment(
                            task_id=t.id,
                            person_id=a.person_id,
                            hours=t.duration_hours,
                        )
                    )
```

`src/planner/app/ports.py` — declare it (domain types are importable;
`ports.py` may not import domain models yet — if adding
`from planner.domain.models import Assignment, Task` creates an import cycle,
STOP and report):

```python
    async def save_project_tasks(
        self,
        project_id: UUID,
        tasks: tuple[DomainTask, ...],
        assignments: tuple[DomainAssignment, ...],
    ) -> None: ...
```

**Verify**: `uv run mypy src/planner/infra/db/repo.py src/planner/app/ports.py --strict` → exit 0

### Step 2: Call it from AddProjectUseCase.execute

In `src/planner/app/add_project.py`, after `create_project` and before
`save_plan_version` (post-014 ordering):

```python
        await self._repo.save_project_tasks(project.id, tasks, plan.assignments)
```

**Verify**: `uv run mypy src/planner/app/add_project.py --strict` → exit 0

### Step 3: Fakes record the call

`tests/unit/app/conftest.py::FakeRepo` — add storage in `__init__`
(`self.saved_tasks: list[tuple] = []`) and:

```python
    async def save_project_tasks(self, project_id, tasks, assignments) -> None:
        self.saved_tasks.append((project_id, tasks, assignments))
```

`tests/unit/bot/test_handler_coverage.py::_FakeRepo` — same two-line addition.

**Verify**: `uv run pytest tests/unit/app tests/unit/bot/test_handler_coverage.py -v` → all pass

### Step 4: Assert persistence in the use-case test

In `tests/unit/app/test_add_project.py`, extend the existing happy-path test
(or add a sibling following its fixture style):

```python
async def test_add_project_persists_tasks_with_schedule():
    ...  # arrange exactly like the file's existing happy-path test
    result = await uc.execute(intent, actor, (person,), template, today=today)
    assert repo.saved_tasks, "tasks must be persisted to the tasks table"
    saved_project_id, saved_tasks, saved_assignments = repo.saved_tasks[0]
    assert saved_project_id == result.project.id
    assert {t.id for t in saved_tasks} == {t.id for t in result.tasks}
    assert len(saved_assignments) == len(result.plan.assignments)
```

**Verify**: `uv run pytest tests/unit/app/test_add_project.py -v` → all pass, including the new test

## Test plan

- New: happy-path AddProject records one `save_project_tasks` call with the
  project id, the full task set, and the plan's assignments.
- Regression: existing `test_add_project.py` tests; cycle test from plan 014
  must still show `repo.saved_tasks == []` on failure (add that assertion to it
  if plan 014's test exists).
- Verification: commands table → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/app src/planner/infra/db/repo.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/app tests/unit/bot/test_handler_coverage.py -v` exits 0
- [ ] `grep -rn "save_project_tasks" src/` shows repo, ports, and add_project sites
- [ ] New test proves tasks+assignments are persisted on success
- [ ] `git status --porcelain` lists only the six in-scope files as modified
- [ ] `plans/README.md` status row for 016 updated

## STOP conditions

Stop and report back if:

- Plan 014 has not landed (`execute` still creates the project before solving).
- ORM `Task` lacks any of the columns used in Step 1
  (`grep -n "class Task" -A 20 src/planner/infra/db/models.py`).
- Importing domain models into `app/ports.py` creates a circular import.
- The ORM `Assignment` primary key is not `(task_id, person_id)` as the
  `assign_task` method implies.

## Maintenance notes

- Re-planning flows (what-if commits, plan 012's supersede path) do NOT update
  these task rows — task dates can drift from the latest committed payload.
  Acceptable for now (boards show the original plan); a future "sync tasks on
  confirm" step is the follow-up if drift becomes visible. Reviewer should be
  aware this is display data, not the capacity source.
- If a task ends up split across people later (allow_two_assignees), the
  single-assignment mapping here needs revisiting.
- Cancelled projects (plan 012) keep their task rows; filtering them out of
  boards is a UI decision deferred with 012's note.
