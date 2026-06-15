# Plan 012: Stop the edit loop from accumulating duplicate projects and orphaned proposed plans

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
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/011-confirm-plan-atomic-transition.md (uses `transition_plan_status`)
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

When an admin clicks "✏️ Правка" on a proposed plan and types a revised
request, the FSM handler re-runs the **full AddProject flow**, which calls
`repo.create_project` again. Every edit iteration therefore creates a brand-new
project row plus a new proposed plan version, while the previous project and
its proposed plan stay behind forever — status "planning"/"proposed", never
cleaned up. `pending_pv_id` is written into FSM state at the start of the edit
and **never read anywhere** (grep-proven: one reference in the codebase, the
write). After a few edits the project list and load views fill with
near-duplicate garbage rows. This plan closes the loop: when an edit produces a
new proposal, the old proposed plan is superseded and its orphaned project is
cancelled.

This plan deliberately does NOT redesign the edit UX (single-message edit
window, "ок"-to-confirm — recorded separately as deferred M7 scope); it only
stops the data pollution.

## Current state

- `src/planner/bot/handlers/confirm.py:47-58` — the write-only state:
  ```python
  @router.callback_query(F.data.startswith("edit:"))
  async def handle_edit(cb: CallbackQuery, state: FSMContext) -> None:
      """Enter FSM edit loop (spec flow step 14): store plan_version_id, await edit text."""
      assert cb.data is not None
      pv_id = cb.data.split(":", 1)[1]
      await state.set_state(PlanEditState.waiting)
      await state.update_data(pending_pv_id=pv_id)
      await cb.answer()
  ```
- `src/planner/bot/handlers/task_router.py:144` — `_handle_text(...) -> None`
  runs the AddProject branch internally and discards the new `pv_id`:
  ```python
      if (
          isinstance(intent, AddProjectIntent)
          and repo is not None
          and solver is not None
          and actor_record is not None
      ):
          text, pv_id = await build_add_project_reply(...)
          kb = _plan_keyboard(pv_id) if pv_id is not None else None
          await message.answer(text, reply_markup=kb)
          return
  ```
- `src/planner/bot/handlers/task_router.py:245-272` — `handle_edit_text` calls
  `_handle_text(...)` then `await state.clear()`; it never reads state data.
- `src/planner/infra/db/repo.py` — after plan 011 there is
  `transition_plan_status(pv_id, from_status, to_status) -> bool`. There is a
  `get_plan_version(pv_id)` returning `PlanVersionRecord(id, project_id,
  status, payload)`. There is **no** `set_project_status` method yet —
  `Project.status` exists as a column (`infra/db/models.py`, near the `title`
  column at line ~169; verify with `grep -n "status" src/planner/infra/db/models.py`).
- `tests/unit/bot/test_handler_coverage.py` — handler test patterns:
  `_message()`, `_FakeParser`, `SimpleNamespace(clear=AsyncMock())` for FSM
  state (see `test_handle_edit_text_routes_and_clears_state`). FSM state fakes
  need `get_data=AsyncMock(return_value={...})` added for the new reads.
- `tests/unit/app/conftest.py` — `FakeRepo` (used via
  `from tests.unit.app.conftest import FakeRepo` in handler tests) gains the
  new repo methods.

## Commands you will need

| Purpose   | Command                                                                              | Expected on success |
|-----------|---------------------------------------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/bot/handlers src/planner/infra/db/repo.py src/planner/app/ports.py --strict` | exit 0 |
| Lint      | `uv run ruff check src tests`                                                         | exit 0              |
| Tests     | `uv run pytest tests/unit/bot/test_handler_coverage.py tests/unit/bot/test_handlers.py -v` | all pass       |

## Scope

**In scope**:
- `src/planner/bot/handlers/task_router.py` (`_handle_text` return value;
  `handle_edit_text` cleanup logic)
- `src/planner/infra/db/repo.py` (add `set_project_status`)
- `src/planner/app/ports.py` (declare `set_project_status`)
- `tests/unit/app/conftest.py` (FakeRepo additions)
- `tests/unit/bot/test_handler_coverage.py` (new/updated tests)

**Out of scope** (do NOT touch):
- `bot/handlers/confirm.py` — `handle_edit` already stores what we need.
- The edit-loop UX (state lifetime, "ок" handling) — deferred M7 scope.
- Deleting project/task rows — cancellation is a status change, never a DELETE.
- `web/` routes.

## Git workflow

- Branch: `advisor/012-edit-loop-supersede`
- Commit message: `fix(bot): supersede old proposal and cancel orphan project on plan edit`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add `set_project_status` to repo + port

`src/planner/infra/db/repo.py` (mirror `set_task_status` at line ~221):

```python
    async def set_project_status(self, project_id: UUID, status: str) -> None:
        async with self._sf() as s, s.begin():
            p = await s.get(Project, project_id)
            if p is not None:
                p.status = status
```

`src/planner/app/ports.py`, next to `set_task_status`:

```python
    async def set_project_status(self, project_id: UUID, status: str) -> None: ...
```

**Verify**: `uv run mypy src/planner/infra/db/repo.py src/planner/app/ports.py --strict` → exit 0

### Step 2: Make `_handle_text` return the new plan-version id

In `src/planner/bot/handlers/task_router.py`, change the signature of
`_handle_text` to `-> UUID | None` and return `pv_id` from the AddProject
branch; every other exit path returns `None` (add explicit `return None` where
bare `return` exists, or leave bare returns — both type-check as `None`; be
consistent with mypy strict). The three existing callers (`handle_voice`,
`handle_task`, `handle_mention_or_dm`) ignore the return value — no change
needed there.

In the AddProject branch:

```python
        text, pv_id = await build_add_project_reply(...)
        kb = _plan_keyboard(pv_id) if pv_id is not None else None
        await message.answer(text, reply_markup=kb)
        return pv_id
```

**Verify**: `uv run mypy src/planner/bot/handlers/task_router.py --strict` → exit 0

### Step 3: Supersede + cancel in `handle_edit_text`

Replace the tail of `handle_edit_text` (currently `await _handle_text(...)`
then `await state.clear()`):

```python
    new_pv_id = await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
    )
    # The edit produced a fresh proposal: retire the one it replaces so the
    # project list does not accumulate near-duplicate planning rows.
    if new_pv_id is not None and repo is not None:
        data = await state.get_data()
        old_raw = data.get("pending_pv_id")
        if old_raw:
            try:
                old_pv_id = UUID(str(old_raw))
            except ValueError:
                old_pv_id = None
            if old_pv_id is not None and old_pv_id != new_pv_id:
                old_pv = await repo.get_plan_version(old_pv_id)
                superseded = await repo.transition_plan_status(
                    old_pv_id, "proposed", "superseded"
                )
                if superseded and old_pv is not None:
                    await repo.set_project_status(old_pv.project_id, "cancelled")
    # Clear edit state so subsequent messages go through the normal handler.
    await state.clear()
```

(`UUID` is already imported in this module.)

**Verify**: `uv run mypy src/planner/bot/handlers/task_router.py --strict` → exit 0

### Step 4: FakeRepo support

In `tests/unit/app/conftest.py`, add to `FakeRepo`:

```python
    async def set_project_status(self, project_id: UUID, status: str) -> None:
        p = self.projects.get(project_id)
        if p is not None:
            self.projects[project_id] = ProjectRecord(
                p.id, p.title, status, p.deadline
            )
```

(`transition_plan_status` exists after plan 011 — STOP if it does not.)

**Verify**: `uv run pytest tests/unit/app -v` → all pass

### Step 5: Handler test

In `tests/unit/bot/test_handler_coverage.py`, extend the edit-handler coverage.
Build on `test_handle_text_add_project_with_repo_sends_keyboard` (which shows
how to wire `FakeRepo` + `GreedySolver` + a template so AddProject really runs)
and `test_handle_edit_text_routes_and_clears_state` (FSM state stubbing):

```python
@pytest.mark.asyncio
async def test_handle_edit_text_supersedes_old_proposal():
    from datetime import timedelta
    from planner.app.add_project import ProjectTemplate, TemplateTaskSpec
    from planner.app.ports import PersonRecord, PlanVersionRecord
    from planner.bot.handlers.task_router import handle_edit_text
    from planner.domain.calendar.rules import WeekendCalendar
    from planner.domain.models import Person
    from planner.domain.solver.greedy import GreedySolver
    from tests.unit.app.conftest import FakeRepo

    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo = FakeRepo()
    repo.solver_people = (andrey,)
    repo.templates = {
        "standard": ProjectTemplate(
            code="standard", tasks=(TemplateTaskSpec(1, "Бриф", 8, (andrey.id,)),)
        )
    }
    # Pre-existing proposed plan + its project (the one being edited).
    old_project_id = uuid4()
    from planner.app.ports import ProjectRecord
    repo.projects[old_project_id] = ProjectRecord(old_project_id, "Старый", "planning", None)
    old_pv = PlanVersionRecord(uuid4(), old_project_id, "proposed", {})
    repo.plan_versions[old_pv.id] = old_pv

    actor_record = PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)
    intent = AddProjectIntent(
        title="Новый", template_code="standard",
        deadline=date.today() + timedelta(days=30),
    )
    msg, answers = _message("правка: новый план")
    state = SimpleNamespace(
        clear=AsyncMock(),
        get_data=AsyncMock(return_value={"pending_pv_id": str(old_pv.id)}),
    )
    await handle_edit_text(
        msg, state, _FakeParser(intent), {"is_admin": True},  # type: ignore[arg-type]
        repo=repo, solver=GreedySolver(WeekendCalendar()), actor_record=actor_record,
    )
    assert repo.plan_versions[old_pv.id].status == "superseded"
    assert repo.projects[old_project_id].status == "cancelled"
    assert state.clear.called
```

Also update `test_handle_edit_text_routes_and_clears_state` and
`test_handle_edit_text_empty_message_ignored`: their `state` stubs need
`get_data=AsyncMock(return_value={})` only if the code path reaches `get_data`
(it does not when `new_pv_id is None` — ClarifyIntent path — so they should
pass unchanged; run them first and only touch them if they fail).

**Verify**: `uv run pytest tests/unit/bot/test_handler_coverage.py -v` → all pass, including the new test

## Test plan

- New: `test_handle_edit_text_supersedes_old_proposal` — old pv → "superseded",
  old project → "cancelled", state cleared.
- Regression: `test_handle_edit_text_routes_and_clears_state` (ClarifyIntent
  path — no supersede attempted), `test_handle_edit_text_empty_message_ignored`,
  and the full `test_handler_coverage.py` file.
- Verification: `uv run pytest tests/unit/bot/test_handler_coverage.py tests/unit/app -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/bot/handlers src/planner/infra/db/repo.py src/planner/app/ports.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/bot/test_handler_coverage.py tests/unit/app -v` exits 0
- [ ] `grep -rn "pending_pv_id" src/` shows BOTH the write (`confirm.py`) and the read (`task_router.py`)
- [ ] New test proves: after an edit produces a new proposal, old pv status is "superseded" and old project status is "cancelled"
- [ ] `git status --porcelain` lists only the five in-scope files as modified
- [ ] `plans/README.md` status row for 012 updated

## STOP conditions

Stop and report back if:

- `transition_plan_status` does not exist on the repo/FakeRepo (plan 011 has
  not landed — this plan depends on it).
- `Project` model has no `status` column
  (`grep -n "status" src/planner/infra/db/models.py` near the Project class).
- `handle_edit_text` / `_handle_text` no longer match the excerpts.
- Changing `_handle_text`'s return type breaks a caller that actually consumes
  the return value (there should be none — report if found).

## Maintenance notes

- Statuses introduced/used here: plan_version "superseded", project
  "cancelled". If a status enum/constraint is ever added at the DB level, these
  two values must be included.
- The cancelled project rows still exist (by design — audit trail). If the
  project list UI should hide cancelled projects, that is a follow-up filter in
  `list_projects`, deliberately not done here.
- Reviewer focus: the supersede only fires when a NEW pv id was actually
  produced and differs from the old; failure of the edit (clarify, solver
  error) must leave the old proposal untouched and still confirmable.
- Deeper fix deferred: ideally an edit would re-plan the SAME project rather
  than create a sibling project. That is a use-case-level redesign
  (AddProject vs. ReplanProject) recorded in the index as future work.
