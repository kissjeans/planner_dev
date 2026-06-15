# Plan 022: /whatif compares against the real plan graph, not a flattened one

> **Executor instructions**: Follow step by step; run every verification before
> moving on. On a "STOP condition", stop and report. Update this plan's row in
> `plans/README.md` when done.
>
> **Drift check (run first)**: working tree was dirty at authoring time. Open
> `src/planner/bot/handlers/whatif.py` and confirm the quoted lines match before
> editing. On a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-15

## Why this matters

`/whatif` lets an admin ask "what happens if I add a person / shift the deadline
/ drop a project" and shows a diff against the current committed plan. But the
baseline it diffs against is reconstructed wrong: the reconstruction throws away
**all task dependencies** (`dependencies=()`) and flattens each task to its
allocated hours with a placeholder name. The greedy solver therefore schedules
the baseline as if every task were independent — it can parallelize tasks that
actually have finish-to-start precedence. Both the "base" and "modified" plans
are computed over this fictitious graph, so the diff the admin acts on is not a
faithful comparison of the real schedule. The feature gives confident, wrong
answers.

## Current state

File: `src/planner/bot/handlers/whatif.py` — the defect is in `_base_request`,
which rebuilds a `PlanRequest` from committed-plan payloads:

```python
# whatif.py:30-53
async def _base_request(repo: RepoPort, solver: SolverPort) -> PlanRequest | None:
    people = await repo.get_solver_people()
    if not people:
        return None
    payloads = await repo.list_committed_plans()
    tasks: list[Task] = []
    for payload in payloads:
        for a in payload.get("assignments", []):
            hours = sum(al["hours"] for al in a.get("allocations", []))
            tasks.append(
                Task(
                    id=UUID(a["task_id"]),
                    name="task",                       # <-- placeholder
                    duration_hours=max(hours, 1),      # <-- flattened
                    allowed_person_ids=(UUID(a["person_id"]),),
                )
            )
    return PlanRequest(
        people=people,
        tasks=tuple(tasks),
        dependencies=(),                               # <-- ALL deps dropped
        horizon_start=date.today(),
    )
```

The use-case itself is correct — it solves whatever request it's handed
(`app/what_if.py:47-50`), so the fix belongs entirely in `_base_request`.

Data available to reconstruct dependencies:
- `repo.get_project_template(code)` returns a template carrying task durations
  and `TemplateDependency` edges (used by `app/add_project.py`).
- The committed payload includes `assignments` with `task_id`/`person_id`. Whether
  it also persists the original dependency graph is **unknown** — verify before
  choosing an approach (see Step 1).

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Tests   | `uv run pytest tests/unit/app/test_what_if.py tests/unit/bot/test_handler_coverage.py` | all pass |
| Types   | `mypy src/planner --strict` | exit 0 |
| Lint    | `ruff check .` | exit 0 |

## Scope

**In scope:**
- `src/planner/bot/handlers/whatif.py`
- Tests: `tests/unit/bot/test_handler_coverage.py` and/or a new test alongside
  `tests/unit/app/test_what_if.py`.

**Out of scope:**
- `src/planner/app/what_if.py` (`WhatIfUseCase`) — correct as-is.
- The greedy solver and `domain/models.py`.
- Persisting a new field on `PlanVersion` **unless** Step 1 proves it's the only
  viable source — if so, that becomes a STOP-and-report (schema/migration is a
  separate plan).

## Git workflow

- Branch: `advisor/022-whatif-faithful-baseline`
- Conventional commits (e.g. `fix(whatif): rebuild baseline with real dependencies and durations`).
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Determine the dependency source (decision gate)

Inspect the committed payload shape (read `infra/db/repo.py::list_committed_plans`
and the codec in `app/add_project.py::deserialize_allocations` /
`app/plan_codec` if present) to answer: **does the persisted payload contain the
task dependency edges, or only assignments/allocations?**

- If the payload carries dependencies → reconstruct them in `_base_request` from
  the payload.
- If it does **not**, but every committed task maps to a project whose template
  has the edges → source durations + `TemplateDependency` edges from
  `repo.get_project_template(...)`, keyed to the committed assignments.
- If neither is reachable without a schema change → **STOP and report**; the fix
  needs a new persisted field (separate migration plan), which is out of scope.

**Verify**: write down (in the PR description or a comment) which source you found.

### Step 2: Rebuild the baseline faithfully

Replace the lossy reconstruction so that:
- each `Task` keeps its real `duration_hours` and real `name` (from the template
  or payload), not `max(hours, 1)` / `"task"`;
- `dependencies` is populated with the real edges (mapped to the same task ids
  used in `tasks`).

Keep the `allowed_person_ids` binding to the committed assignee (so the base
plan reproduces the committed assignment), and keep `horizon_start=date.today()`.

**Verify**: `mypy src/planner --strict` → exit 0.

### Step 3: Regression test the dependency fidelity

Add a unit test: build a committed plan whose tasks have a known FS dependency
(task B depends on task A), run `_base_request` (or the handler down to the
diff), and assert the reconstructed base plan does **not** schedule B in parallel
with A — i.e. B starts on/after A's end. Model the fake repo after the existing
`tests/unit/bot/test_handler_coverage.py` / `tests/unit/app/test_what_if.py`
fixtures.

**Verify**: `uv run pytest tests/unit/app/test_what_if.py tests/unit/bot/test_handler_coverage.py` → all pass, new case included.

## Test plan

- New test asserts dependency ordering survives reconstruction (the core
  regression).
- If existing `/whatif` handler tests assert on the old flattened diff output,
  update them to the corrected output and note the change in the PR.
- Verification: `uv run pytest tests/unit/app/test_what_if.py tests/unit/bot/test_handler_coverage.py` → all pass.

## Done criteria

ALL must hold:

- [ ] `grep -n "dependencies=()" src/planner/bot/handlers/whatif.py` returns nothing (real edges now populated), OR a STOP was reported per Step 1.
- [ ] `grep -n 'name="task"' src/planner/bot/handlers/whatif.py` returns nothing.
- [ ] A test asserts a dependent task is not parallelized in the reconstructed base plan.
- [ ] `uv run pytest tests/unit/app/test_what_if.py tests/unit/bot/test_handler_coverage.py` → exit 0.
- [ ] `ruff check .` → exit 0; `mypy src/planner --strict` → exit 0.
- [ ] No files outside the in-scope list modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

- Step 1 concludes the dependency graph is not recoverable without persisting a
  new field — STOP; recommend a follow-up schema/migration plan and do not widen
  scope here.
- The committed payload’s `task_id`s don't line up with the template’s task ids
  (no stable key to map edges) — STOP and report the id mismatch.
- Existing diff-output tests change in ways that suggest the solver itself
  behaves unexpectedly on dependencies — STOP rather than rewriting solver tests.

## Maintenance notes

- The cleanest long-term fix is to persist the original `PlanRequest` (or its
  dependency edges) with the `PlanVersion` at confirm time, so what-if never has
  to reconstruct. If that field is added later, `_base_request` should read it
  directly and this reconstruction can be deleted.
- Reviewer: scrutinize the task-id ↔ dependency-edge mapping; an off-by-one there
  silently reintroduces the bug.
- Note: `switch_to_lite` is intentionally a no-op in `apply_operation`
  (`app/what_if.py:37-40`) — out of scope, leave it.
