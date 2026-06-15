# Plan 010: Harden the voice handler — no assert control flow, cap download size

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: The repo had uncommitted working-tree changes
> when this plan was written, so a `git diff` against the commit SHA will NOT
> reflect reality. Instead, open `src/planner/bot/handlers/task_router.py` and
> confirm the quoted excerpt matches the live code. On any mismatch, treat it
> as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (coordinate with 003, which also edits `task_router.py`)
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-11 (against uncommitted working tree)

## Why this matters

The voice handler (QA report L6) has two latent defects (latent because STT is
currently disabled — no OpenAI key):

1. It uses `assert` for control flow (`assert file.file_path is not None`,
   `assert audio is not None`). Python run with `-O` strips asserts, turning
   these into `AttributeError`/`TypeError` on bad input.
2. It downloads the entire voice file into memory (`audio.read()`) with no size
   limit — a large/abusive upload becomes a memory-pressure vector once STT is
   enabled.

This plan replaces the asserts with explicit checks + friendly replies and adds
a size cap before download.

## Current state

`src/planner/bot/handlers/task_router.py`:

```python
# task_router.py:196
@router.message(F.voice)
async def handle_voice(
    message: Message,
    parser: IntentParserPort,
    actor: dict[str, Any],
    stt: STTPort | None = None,
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
    actor_record: PersonRecord | None = None,
    explain_uc: ExplainPlanUseCase | None = None,
) -> None:
    if stt is None or message.voice is None or message.bot is None:
        await message.answer("Голосовые сообщения не поддерживаются — напиши текстом.")
        return
    bot = message.bot
    file = await bot.get_file(message.voice.file_id)
    assert file.file_path is not None
    audio = await bot.download_file(file.file_path)
    assert audio is not None
    text = await stt.transcribe(audio.read(), "voice.ogg")
    if not text:
        await message.answer("Не удалось распознать голос — напиши текстом.")
        return
    await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
    )
```

`tests/unit/bot/test_handler_coverage.py` exercises this handler. Note the
voice tests build `msg.voice = SimpleNamespace(file_id="abc")` — **no
`file_size` attribute**. After adding a size check that reads
`message.voice.file_size`, those `SimpleNamespace` objects need a `file_size`
field, so the existing voice tests must be updated:

```python
# test_handler_coverage.py:488  test_handle_voice_with_stt_transcribes_and_routes
# test_handler_coverage.py:513  test_handle_voice_stt_returns_empty_string
    msg.voice = SimpleNamespace(file_id="abc")  # ← needs file_size added
```

Real aiogram `Voice` objects always have `file_size: int | None`.

## Commands you will need

| Purpose   | Command                                                          | Expected on success |
|-----------|------------------------------------------------------------------|---------------------|
| Typecheck | `uv run mypy src/planner/bot/handlers/task_router.py --strict`   | exit 0, no errors   |
| Lint      | `uv run ruff check src tests`                                    | exit 0              |
| Tests     | `uv run pytest tests/unit/bot/test_handler_coverage.py -v`       | all pass            |

## Scope

**In scope**:
- `src/planner/bot/handlers/task_router.py` (`handle_voice` only)
- `tests/unit/bot/test_handler_coverage.py`

**Out of scope** (do NOT touch):
- `_handle_text` — that is plan 003's region; only call it as `handle_voice`
  already does.
- The STT adapter (`infra/stt/whisper.py`).
- The `F.voice` router decorator / handler registration.

## Git workflow

- Branch: `advisor/010-voice-handler-hardening`
- Commit message: `fix(bot): replace asserts and cap size in voice handler`
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add a size constant and replace asserts with explicit checks

In `src/planner/bot/handlers/task_router.py`, add a module-level constant near
the top (after `router = Router(name="task")`):

```python
_MAX_VOICE_BYTES = 20 * 1024 * 1024  # 20 MB cap on voice downloads
```

Rewrite the body of `handle_voice` from `bot = message.bot` onward:

```python
    bot = message.bot
    if message.voice.file_size and message.voice.file_size > _MAX_VOICE_BYTES:
        await message.answer("Голосовое слишком большое — пришли покороче или текстом.")
        return
    file = await bot.get_file(message.voice.file_id)
    if file.file_path is None:
        await message.answer("Не удалось получить голосовое сообщение — напиши текстом.")
        return
    audio = await bot.download_file(file.file_path)
    if audio is None:
        await message.answer("Не удалось скачать голосовое сообщение — напиши текстом.")
        return
    text = await stt.transcribe(audio.read(), "voice.ogg")
    if not text:
        await message.answer("Не удалось распознать голос — напиши текстом.")
        return
    await _handle_text(
        message, text, parser, actor,
        repo=repo, solver=solver, actor_record=actor_record, explain_uc=explain_uc,
    )
```

**Verify**: `uv run mypy src/planner/bot/handlers/task_router.py --strict` → exit 0; `grep -n "assert " src/planner/bot/handlers/task_router.py` returns nothing

### Step 2: Update existing voice tests to include file_size

In `tests/unit/bot/test_handler_coverage.py`, add `file_size` to the two
`msg.voice` SimpleNamespaces in
`test_handle_voice_with_stt_transcribes_and_routes` and
`test_handle_voice_stt_returns_empty_string`:

```python
    msg.voice = SimpleNamespace(file_id="abc", file_size=1000)
```

**Verify**: `uv run pytest tests/unit/bot/test_handler_coverage.py -k voice -v` → existing voice tests pass

### Step 3: Add tests for the new guards

Add to `tests/unit/bot/test_handler_coverage.py`:

```python
@pytest.mark.asyncio
async def test_handle_voice_rejects_oversized():
    from planner.bot.handlers.task_router import handle_voice
    intent = ClarifyIntent(question="X")
    msg, answers = _message()
    msg.voice = SimpleNamespace(file_id="abc", file_size=50 * 1024 * 1024)  # 50 MB
    msg.bot = SimpleNamespace()  # must not be used — size check is first
    stt = SimpleNamespace(transcribe=AsyncMock())
    await handle_voice(msg, _FakeParser(intent), {"is_admin": False}, stt=stt)  # type: ignore[arg-type]
    assert "слишком большое" in answers.calls[0]
    assert not stt.transcribe.called


@pytest.mark.asyncio
async def test_handle_voice_missing_file_path():
    from planner.bot.handlers.task_router import handle_voice
    intent = ClarifyIntent(question="X")
    msg, answers = _message()
    msg.voice = SimpleNamespace(file_id="abc", file_size=1000)
    file_obj = SimpleNamespace(file_path=None)
    msg.bot = SimpleNamespace(get_file=AsyncMock(return_value=file_obj))
    stt = SimpleNamespace(transcribe=AsyncMock())
    await handle_voice(msg, _FakeParser(intent), {"is_admin": False}, stt=stt)  # type: ignore[arg-type]
    assert "Не удалось получить" in answers.calls[0]
```

(`AsyncMock` and `ClarifyIntent` are already imported in this file.)

**Verify**: `uv run pytest tests/unit/bot/test_handler_coverage.py -v` → all pass, including 2 new tests

## Test plan

- Update: the two existing voice tests get `file_size=1000` on `msg.voice`.
- New: oversized voice → rejected before STT (transcribe not called); missing
  `file_path` → friendly reply, no crash.
- Regression: `test_handle_voice_no_stt_replies_unsupported` (no STT) still
  passes unchanged.
- Verification: `uv run pytest tests/unit/bot/test_handler_coverage.py -v` → all pass.

## Done criteria

ALL must hold:

- [ ] `uv run mypy src/planner/bot/handlers/task_router.py --strict` exits 0
- [ ] `uv run ruff check src tests` exits 0
- [ ] `uv run pytest tests/unit/bot/test_handler_coverage.py -v` exits 0; 2 new tests pass
- [ ] `grep -n "assert " src/planner/bot/handlers/task_router.py` returns nothing
- [ ] Oversized voice is rejected before `stt.transcribe` is called
- [ ] `git status --porcelain` lists only the two in-scope files as modified
- [ ] `plans/README.md` status row for 010 updated

## STOP conditions

Stop and report back if:

- `handle_voice` no longer matches the "Current state" excerpt (e.g. plan 003
  changed nearby code — re-read; the asserts should still be present to remove).
- aiogram's `Voice` type in this repo has no `file_size` attribute (different
  aiogram version) — report so the size-check source can be revised.
- Removing an assert reveals that some caller relied on it raising — report.

## Maintenance notes

- The 20 MB cap is conservative (Telegram voice messages are far smaller);
  adjust `_MAX_VOICE_BYTES` if legitimate messages are ever rejected.
- `audio.read()` still loads the (now size-capped) file fully into memory. If
  STT is later switched to a streaming API, revisit to stream instead of
  buffering.
- This handler is dormant until an OpenAI key is configured; the fix is
  preventive. A reviewer should confirm the no-STT early-return path is
  unchanged.
- Ordering: plan 003 also edits `task_router.py` (a gate inside `_handle_text`).
  Different function; execute in numeric order and rely on the drift check.
