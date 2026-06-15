# Plan 003: Require a known sender before processing any chat message (close the open write path)

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

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

The bot processes messages from anyone who can DM it. Two consequences (QA
report H2 + part of H1):

1. **Open write path.** `CaptureTaskIntent` is handled *before* the admin
   write-gate and is not in `WRITE_KINDS`, so any sender — including a complete
   stranger — can create projects and tasks in the DB (`capture_task` auto-creates
   projects from arbitrary names).
2. **Budget burn.** Every message reaches the LLM parser, so a stranger's
   messages cost Anthropic API calls.

This plan adds a single **known-sender gate** at the top of `_handle_text` (the
one chokepoint every text/voice path funnels through). Only resolved team
members (`actor_record` set by `ActorMiddleware`) or admins get their messages
parsed and acted on. Strangers get one polite refusal and nothing is written or
sent to the LLM. (Rate-limiting for *known* users is a separate concern — see
plan 004.)

## Current state

- `src/planner/bot/handlers/task_router.py` — `_handle_text` runs the parser
  immediately and handles capture before the admin gate:
  ```python
  # task_router.py:144
  async def _handle_text(
      message: Message,
      text: str,
      parser: IntentParserPort,
      actor: dict[str, Any],
      *,
      repo: RepoPort | None = None,
      solver: SolverPort | None = None,
      actor_record: PersonRecord | None = None,
      explain_uc: ExplainPlanUseCase | None = None,
  ) -> None:
      ctx = ChatContext(today=date.today())
      intent = await parser.parse(text, ctx)

      if isinstance(intent, ClarifyIntent):
          await message.answer(describe_intent(intent))
          return

      if isinstance(intent, CaptureTaskIntent):
          if repo is None:
              await message.answer(describe_intent(intent))
              return
          await message.answer(
              await build_capture_reply(intent, repo=repo, actor_record=actor_record)
          )
          return

      if not can_execute(intent.kind, actor.get("is_admin", False)):
          await message.answer("Только админ может править план.")
          return
      ...
  ```
- `src/planner/bot/middlewares/permissions.py` — `ActorMiddleware` sets
  `data["actor_record"]` ONLY when `repo.get_person_by_tg_id(tg_id)` resolves a
  person; otherwise it is absent. `actor["is_admin"]` is True for IDs in
  `admin_ids` or persons flagged admin. So **`actor_record is None and not
  actor["is_admin"]` is exactly "unknown sender".**
- `src/planner/app/capture_task.py` — `CaptureTaskUseCase.execute` creates a
  project (or Inbox stub) and a task. This is the write that must be gated.
- `tests/unit/bot/test_handler_coverage.py` — existing tests exercise
  `_handle_text`. Note especially `test_handle_text_capture_writes_to_db`
  (line ~165): it passes `{"is_admin": False}` with **no** `actor_record` and
  asserts the write happens. After this plan that input is an "unknown sender"
  and must be blocked, so this test MUST be updated to pass a known
  `actor_record`. The `repo=None` echo tests
  (`test_handle_text_capture_no_repo_echoes`,
  `test_handle_text_clarify_replies_question`) pass no repo and must remain
  unblocked.

`PersonRecord` is importable from `planner.app.ports` (fields:
`id: UUID`, `name`, `is_admin: bool = False`).

## Commands you will need

| Purpose   | Command                                                          | Expected on success |
|-----------|------------------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/bot/handlers/task_router.py --strict`   | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                                    | exit 0              |
| Tests     | `uv run pytest tests/unit/bot/test_handler_coverage.py -v`       | all pass            |

## Scope

**In scope**:
- `src/planner/bot/handlers/task_router.py` (add the gate in `_handle_text` only)
- `tests/unit/bot/test_handler_coverage.py`

**Out of scope** (do NOT touch):
- `src/planner/bot/middlewares/permissions.py` — `ActorMiddleware` already
  resolves the data this gate reads; do not move the gate into the middleware
  (it would also block `/start`, which must greet anyone).
- `src/planner/app/capture_task.py` — the use-case is fine; we gate its caller.
- `WRITE_KINDS` in `domain/intent.py` — leave as-is; this plan does not
  reclassify capture as a write-kind, it blocks unknown senders upstream.

## Git workflow

- Branch: `advisor/003-known-sender-gate`
- Commit message: `fix(security): gate chat handling behind known-sender check`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add the known-sender gate at the top of _handle_text

In `src/planner/bot/handlers/task_router.py`, insert the gate as the FIRST
statements in `_handle_text`, before `ctx = ChatContext(...)`:

```python
    # Known-sender gate (spec 16 + QA H1/H2): only resolved team members or
    # admins may have their messages parsed/acted on. This blocks strangers
    # from writing to the DB and from spending the LLM budget. When repo is
    # None we are in degraded/echo mode (no DB) — skip the gate so the bot can
    # still interpret messages offline.
    if repo is not None and actor_record is None and not actor.get("is_admin", False):
        await message.answer(
            "Не узнал тебя — я отвечаю только участникам команды. "
            "Попроси администратора добавить тебя."
        )
        return
    ctx = ChatContext(today=date.today())
    intent = await parser.parse(text, ctx)
    ...
```

**Verify**: `uv run mypy src/planner/bot/handlers/task_router.py --strict` → exit 0

### Step 2: Update the capture-writes test to use a known sender

In `tests/unit/bot/test_handler_coverage.py`, update
`test_handle_text_capture_writes_to_db` so the actor is a known team member
(otherwise it is now correctly blocked). Add an `actor_record`:

```python
@pytest.mark.asyncio
async def test_handle_text_capture_writes_to_db():
    from planner.app.ports import PersonRecord
    msg, answers = _message()
    repo = _FakeRepo()
    intent = CaptureTaskIntent(
        task_title="подготовить бриф", project_name="МТС", assignee_name="Призрак"
    )
    actor_record = PersonRecord(id=uuid4(), name="Андрей", is_admin=False)
    await _handle_text(
        msg, "подготовить бриф по мтс", _FakeParser(intent),  # type: ignore[arg-type]
        {"is_admin": False}, repo=repo, actor_record=actor_record,  # type: ignore[arg-type]
    )
    assert "Записал" in answers.calls[0]
    assert repo.captured_tasks == ["подготовить бриф"]
    assert repo.assignments == []
```

**Verify**: `uv run pytest tests/unit/bot/test_handler_coverage.py::test_handle_text_capture_writes_to_db -v` → passes

### Step 3: Add a test proving unknown senders are blocked

Add a new test in the same file:

```python
@pytest.mark.asyncio
async def test_handle_text_unknown_sender_blocked_no_write():
    msg, answers = _message()
    repo = _FakeRepo()
    intent = CaptureTaskIntent(task_title="запиши задачу")
    # No actor_record, not admin → unknown sender.
    await _handle_text(
        msg, "запиши задачу", _FakeParser(intent),  # type: ignore[arg-type]
        {"is_admin": False}, repo=repo,  # type: ignore[arg-type]
    )
    assert "Не узнал тебя" in answers.calls[0]
    assert repo.captured_tasks == []  # nothing written
```

**Verify**: `uv run pytest tests/unit/bot/test_handler_coverage.py -v` → all pass, including the new test

## Test plan

- Update: `test_handle_text_capture_writes_to_db` → add a known `actor_record`
  so the write still happens for team members.
- New: `test_handle_text_unknown_sender_blocked_no_write` → unknown sender gets
  the refusal and `repo.captured_tasks` stays empty.
- Regression to confirm still pass: `test_handle_text_capture_no_repo_echoes`
  and `test_handle_text_clarify_replies_question` (both pass `repo=None`, so the
  gate is skipped), and `test_handle_text_write_op_blocked_for_non_admin`
  (passes `repo=None`, reaches the admin gate unchanged).
- Verification: `uv run pytest tests/unit/bot/test_handler_coverage.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/bot/handlers/task_router.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/bot/test_handler_coverage.py -v` exits 0
- [ ] A test proves an unknown sender (no `actor_record`, not admin) writes nothing
- [ ] The gate is placed inside `_handle_text` only — `grep -n "Не узнал тебя" src/planner/bot/handlers/task_router.py` returns exactly one line
- [ ] `git status --porcelain` lists only the two in-scope files as modified
- [ ] `plans/README.md` status row for 003 updated

## STOP conditions

Stop and report back if:

- `_handle_text`'s signature no longer has `actor_record` / `repo` keyword
  parameters as shown.
- `ActorMiddleware` no longer sets `actor_record` (the "known sender" signal
  this plan depends on is gone).
- Updating `test_handle_text_capture_writes_to_db` requires changing the
  `_FakeRepo` class itself (it should not — only the test's call needs the
  `actor_record` argument).
- Verification fails twice after a reasonable fix attempt.

## Maintenance notes

- Policy choice baked in here: **capture is open to all team members** (not just
  admins), only strangers are blocked. If the team later wants capture to be
  admin-only too, add `capture_task` to `WRITE_KINDS` instead of changing this
  gate.
- This is the allowlist half of the original H1 finding. Plan 004 adds
  per-user rate-limiting for the throttle half; the two are complementary and
  touch different code (this one: `_handle_text`; 004: a new middleware +
  `runner.py`).
- A reviewer should confirm `/start` (a separate handler) still responds to
  unknown users so they know how to get added.
