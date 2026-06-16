# Audio UX + Confirm Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make voice handling feel responsive (no silent multi-minute waits) and close the last intent gap (short agreement → confirm). Spec-aligned: voice intake (§flow) + "подтвердить" is a core verb (§interface).

**Architecture:** Three contained changes. (A) `FasterWhisperSTT.warmup()` pre-loads the model at startup so the first voice isn't slow. (B) `handle_voice` sends a "🎙 Распознаю…" ack and wraps transcription in `asyncio.wait_for` so it never hangs silently. (C) prompt maps short agreement words ("ок"/"да"/"подтверждаю") to `confirm`. Plus eval expansion to measure.

**Tech Stack:** Python 3.12, aiogram, faster-whisper, anthropic, pytest/pytest-asyncio.

---

## Task 1: STT startup warmup

**Files:** Modify `src/planner/infra/stt/faster_whisper.py`, `src/planner/main.py`; Test `tests/unit/test_faster_whisper_stt.py`

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_faster_whisper_stt.py`:

```python
@pytest.mark.asyncio
async def test_warmup_loads_model(mock_model_cls):
    from planner.infra.stt.faster_whisper import FasterWhisperSTT

    cls, _model = mock_model_cls
    stt = FasterWhisperSTT()
    await stt.warmup()
    cls.assert_called_once_with("small", device="cpu", compute_type="int8")


@pytest.mark.asyncio
async def test_warmup_swallows_errors(mock_model_cls):
    """Warmup is best-effort — a load failure must not raise."""
    cls, _model = mock_model_cls
    cls.side_effect = RuntimeError("no model")
    from planner.infra.stt.faster_whisper import FasterWhisperSTT

    await FasterWhisperSTT().warmup()  # must not raise
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/unit/test_faster_whisper_stt.py::test_warmup_loads_model -v`
Expected: FAIL — `FasterWhisperSTT` has no `warmup`.

- [ ] **Step 3: Implement**

(a) In `src/planner/infra/stt/faster_whisper.py`, add this method to `FasterWhisperSTT` (after `_transcribe_sync`, before `transcribe`):

```python
    async def warmup(self) -> None:
        """Pre-load the model so the first voice message isn't slow."""
        try:
            await asyncio.to_thread(self._load_model)
            log.info("faster_whisper_warmed", model=self._model_size)
        except Exception as exc:  # noqa: BLE001 — warmup is best-effort
            log.warning("faster_whisper_warmup_failed", error=str(exc))
```

(b) In `src/planner/main.py`, immediately after the dispatcher is built (`dp = build_dispatcher(...)`, line ~62), add:

```python
    stt = dp.workflow_data.get("stt")
    if stt is not None:
        asyncio.create_task(stt.warmup())
```

- [ ] **Step 4: Run tests + import smoke**

Run: `uv run pytest tests/unit/test_faster_whisper_stt.py -v && uv run python -c "import planner.main; print('main ok')"`
Expected: PASS; `main ok`.

- [ ] **Step 5: Commit**

```bash
git add src/planner/infra/stt/faster_whisper.py src/planner/main.py tests/unit/test_faster_whisper_stt.py
git commit -m "feat: warm faster-whisper model at startup"
```

---

## Task 2: voice ack + transcription timeout

**Files:** Modify `src/planner/bot/handlers/task_router.py`; Test `tests/unit/bot/test_handler_coverage.py`

**Context:** `handle_voice` currently calls `text = await stt.transcribe(...)` with no feedback and no timeout. Add `import asyncio` (top of file) and `_STT_TIMEOUT_S = 60` (near `_MAX_VOICE_BYTES`). Replace the transcription block.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/bot/test_handler_coverage.py` (match the file's existing style for building a fake `message`/`stt`):

```python
@pytest.mark.asyncio
async def test_handle_voice_sends_ack():
    """User gets a '🎙 Распознаю…' ack before transcription completes."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from planner.bot.handlers.task_router import handle_voice

    sent = []
    ack = SimpleNamespace(delete=AsyncMock())
    msg = SimpleNamespace(
        voice=SimpleNamespace(file_size=100, file_id="f"),
        bot=SimpleNamespace(
            get_file=AsyncMock(return_value=SimpleNamespace(file_path="p")),
            download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"x")),
        ),
        answer=AsyncMock(side_effect=lambda *a, **k: (sent.append(a[0]), ack)[1]),
    )

    class _P:  # parser; capture path not exercised here
        async def parse(self, text, ctx):
            from planner.domain.intent import ClarifyIntent
            return ClarifyIntent(question="x")

    stt = SimpleNamespace(transcribe=AsyncMock(return_value="загрузка"))
    await handle_voice(msg, _P(), {"is_admin": False}, stt=stt)  # type: ignore[arg-type]
    assert any("Распозна" in s for s in sent)


@pytest.mark.asyncio
async def test_handle_voice_timeout_replies(monkeypatch):
    """A slow transcription times out and tells the user, not hangs."""
    import asyncio as _aio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from planner.bot.handlers import task_router

    monkeypatch.setattr(task_router, "_STT_TIMEOUT_S", 0.01)
    sent = []
    ack = SimpleNamespace(delete=AsyncMock())
    msg = SimpleNamespace(
        voice=SimpleNamespace(file_size=100, file_id="f"),
        bot=SimpleNamespace(
            get_file=AsyncMock(return_value=SimpleNamespace(file_path="p")),
            download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"x")),
        ),
        answer=AsyncMock(side_effect=lambda *a, **k: (sent.append(a[0]), ack)[1]),
    )

    async def _slow(*a, **k):
        await _aio.sleep(1)
        return "never"

    stt = SimpleNamespace(transcribe=_slow)
    await task_router.handle_voice(msg, object(), {"is_admin": False}, stt=stt)  # type: ignore[arg-type]
    assert any("Долго распознаю" in s for s in sent)
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/unit/bot/test_handler_coverage.py -v -k "handle_voice_sends_ack or handle_voice_timeout"`
Expected: FAIL — no ack / no timeout handling yet.

- [ ] **Step 3: Implement** — in `src/planner/bot/handlers/task_router.py`:

(a) Add `import asyncio` with the other stdlib imports.

(b) Add the timeout constant next to `_MAX_VOICE_BYTES`:

```python
_STT_TIMEOUT_S = 60  # past this, ask the user to retry — never hang silently
```

(c) In `handle_voice`, replace the current block:

```python
    text = await stt.transcribe(audio.read(), "voice.ogg")
    if not text:
        await message.answer("Не удалось распознать голос — напиши текстом.")
        return
```

with:

```python
    ack = await message.answer("🎙 Распознаю…")
    timed_out = False
    try:
        text = await asyncio.wait_for(
            stt.transcribe(audio.read(), "voice.ogg"), _STT_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        text, timed_out = None, True
    try:
        await ack.delete()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass
    if not text:
        await message.answer(
            "Долго распознаю — пришли покороче или текстом."
            if timed_out
            else "Не удалось распознать голос — напиши текстом."
        )
        return
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/unit/bot/test_handler_coverage.py -v`
Expected: PASS (all, including pre-existing voice tests — verify none regressed; if a pre-existing test asserted the exact old failure flow, update it minimally to the new flow).

- [ ] **Step 5: Commit**

```bash
git add src/planner/bot/handlers/task_router.py tests/unit/bot/test_handler_coverage.py
git commit -m "feat: voice ack + transcription timeout (no silent waits)"
```

---

## Task 3: short agreement → confirm

**Files:** Modify `src/planner/infra/llm/prompts.py`; Test `tests/unit/infra/test_claude_parser.py`

- [ ] **Step 1: Write the failing test** — append:

```python
def test_intent_prompt_covers_short_confirm():
    p = INTENT_SYSTEM_PROMPT.lower()
    assert "подтвержд" in p
    for w in ("ок", "да"):
        assert w in p
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/unit/infra/test_claude_parser.py::test_intent_prompt_covers_short_confirm -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `src/planner/infra/llm/prompts.py`, replace the `confirm` bullet (`- confirm: подтвердить последний предложенный план.`) with:

```python
- confirm: подтвердить последний предложенный план. Короткое согласие —
  «ок», «ok», «да», «ага», «подтверждаю», «согласен», «го» — это confirm
  (НЕ clarify).
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/unit/infra/test_claude_parser.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/planner/infra/llm/prompts.py tests/unit/infra/test_claude_parser.py
git commit -m "fix: short agreement words map to confirm"
```

---

## Task 4: expand eval set

**Files:** Modify `tests/eval/test_intent_eval.py`

- [ ] **Step 1: Extend `CASES`** — in `tests/eval/test_intent_eval.py`, add these tuples to the `CASES` list (keep the existing ones):

```python
    ("ок", "confirm"),
    ("да, подтверждаю", "confirm"),
    ("Поставь Андрея и Рай сделать ресёрч по МТС через 3 дня", "capture_task"),
    ("Ты изменила загрузку других участников команды?", "load"),
```

- [ ] **Step 2: Verify it still collects + skips by default**

Run: `uv run pytest tests/eval --collect-only -q`
Expected: collects 12 items, no marker warnings.

Run: `uv run pytest tests/eval -q`
Expected: 12 skipped (no `RUN_LLM_EVAL`).

- [ ] **Step 3: Commit**

```bash
git add tests/eval/test_intent_eval.py
git commit -m "test: expand intent eval with confirm + multi-assignee cases"
```

---

## Task 5: full verification

- [ ] **Step 1:** `uv run pytest -q` → all pass (eval skipped). Report counts.
- [ ] **Step 2:** `uv run ruff check src tests && uv run mypy src` → clean.

(Do NOT run the live eval — that's the human's measurement step after merge.)

---

## Notes
- Model stays `small` — `medium` on CPU is 10-30 s/clip and would make audio feel worse, not better.
- The ack message is deleted after transcription to avoid clutter; deletion is best-effort.
- `asyncio.wait_for` can't kill the worker thread; on timeout the thread finishes in the background but the user is no longer left hanging.
