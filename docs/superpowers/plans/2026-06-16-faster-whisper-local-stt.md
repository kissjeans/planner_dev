# Local faster-whisper STT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace cloud OpenAI Whisper STT with a free local `faster-whisper` model so Telegram voice messages transcribe with no API key.

**Architecture:** The existing `STTPort` protocol stays the contract; only the adapter behind it swaps. A new `FasterWhisperSTT` lazy-loads the Whisper `small` model on CPU (int8) and runs the blocking transcription inside `asyncio.to_thread`. The bot voice handler and downstream routing are unchanged.

**Tech Stack:** Python 3.12, `faster-whisper` (CTranslate2 + bundled PyAV for ogg/opus decode), aiogram, structlog, pytest/pytest-asyncio.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/planner/infra/stt/ports.py` | NEW — `STTPort` protocol (moved out of `whisper.py`, matches `infra/llm/ports.py`) |
| `src/planner/infra/stt/faster_whisper.py` | NEW — `FasterWhisperSTT` adapter |
| `src/planner/infra/stt/whisper.py` | DELETE — old OpenAI adapter |
| `src/planner/bot/runner.py` | Wire `FasterWhisperSTT` unconditionally |
| `src/planner/bot/handlers/task_router.py` | Import `STTPort` from `ports` |
| `src/planner/settings.py` | Remove `openai_api_key` field |
| `src/planner/main.py` | Startup log `stt="faster-whisper"` |
| `pyproject.toml` | Remove `openai`, add `faster-whisper` |
| `.env`, `.env.example` | Remove `OPENAI_API_KEY` |
| `tests/unit/test_faster_whisper_stt.py` | NEW — adapter unit tests (replaces `test_whisper_stt.py`) |
| `tests/unit/test_whisper_stt.py` | DELETE |
| `tests/unit/bot/test_runner.py` | Replace 2 openai-gated tests with 1 always-wired test |
| `tests/unit/test_settings.py` | Drop openai-optional test |

---

## Task 1: Add faster-whisper dependency, remove openai

**Files:**
- Modify: `pyproject.toml:7-31` (dependencies array)

- [ ] **Step 1: Edit `pyproject.toml` dependencies**

Remove the line `    "openai",` and add `    "faster-whisper",`. Resulting block (relevant lines):

```toml
    "anthropic",
    "instructor",
    "faster-whisper",
    "httpx",
```

- [ ] **Step 2: Install and run import smoke test**

Run:
```bash
uv sync && uv run python -c "import faster_whisper; import instructor; import planner.main; print('imports ok')"
```
Expected: `imports ok`.

If `import instructor` fails because it needs `openai`, re-add `    "openai",` to `pyproject.toml`, re-run `uv sync`, and re-run the smoke test until it prints `imports ok`. (Adapter code is removed regardless; this only decides whether the `openai` package stays installed.)

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add faster-whisper, drop openai dep"
```

---

## Task 2: Extract STTPort into ports.py

**Files:**
- Create: `src/planner/infra/stt/ports.py`
- Modify: `src/planner/bot/handlers/task_router.py:43`

- [ ] **Step 1: Create `src/planner/infra/stt/ports.py`**

```python
"""Speech-to-text port (spec section 2 / 4.7)."""

from __future__ import annotations

from typing import Protocol


class STTPort(Protocol):
    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str | None: ...
```

- [ ] **Step 2: Repoint the handler import**

In `src/planner/bot/handlers/task_router.py`, change line 43:

```python
from planner.infra.stt.whisper import STTPort
```
to:
```python
from planner.infra.stt.ports import STTPort
```

- [ ] **Step 3: Verify the package still imports**

Run: `uv run python -c "import planner.bot.handlers.task_router; print('ok')"`
Expected: `ok` (the old `whisper.py` still exists at this point, so nothing else breaks).

- [ ] **Step 4: Commit**

```bash
git add src/planner/infra/stt/ports.py src/planner/bot/handlers/task_router.py
git commit -m "refactor: move STTPort into stt/ports.py"
```

---

## Task 3: FasterWhisperSTT adapter (TDD)

**Files:**
- Test: `tests/unit/test_faster_whisper_stt.py`
- Create: `src/planner/infra/stt/faster_whisper.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_faster_whisper_stt.py`:

```python
"""Unit tests for FasterWhisperSTT adapter (spec section 4.7)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _segments(*texts):
    """faster-whisper returns (segments_iterable, info)."""
    segs = [SimpleNamespace(text=t) for t in texts]
    return segs, SimpleNamespace(language="ru")


@pytest.fixture()
def mock_model_cls():
    """Patch faster_whisper.WhisperModel so no model is downloaded/loaded."""
    model = MagicMock()
    model.transcribe.return_value = _segments(" привет", " мир")
    cls = MagicMock(return_value=model)
    with patch("faster_whisper.WhisperModel", cls):
        yield cls, model


@pytest.mark.asyncio
async def test_transcribe_returns_joined_text(mock_model_cls):
    from planner.infra.stt.faster_whisper import FasterWhisperSTT

    _cls, _model = mock_model_cls
    stt = FasterWhisperSTT()
    result = await stt.transcribe(b"audio_bytes", "voice.ogg")
    assert result == "привет мир"


@pytest.mark.asyncio
async def test_transcribe_passes_language_ru(mock_model_cls):
    from planner.infra.stt.faster_whisper import FasterWhisperSTT

    _cls, model = mock_model_cls
    stt = FasterWhisperSTT()
    await stt.transcribe(b"data", "clip.ogg")
    model.transcribe.assert_called_once()
    assert model.transcribe.call_args.kwargs["language"] == "ru"


@pytest.mark.asyncio
async def test_model_loaded_with_cpu_int8_small(mock_model_cls):
    from planner.infra.stt.faster_whisper import FasterWhisperSTT

    cls, _model = mock_model_cls
    stt = FasterWhisperSTT()
    await stt.transcribe(b"data")
    cls.assert_called_once_with("small", device="cpu", compute_type="int8")


@pytest.mark.asyncio
async def test_model_loaded_once_and_cached(mock_model_cls):
    from planner.infra.stt.faster_whisper import FasterWhisperSTT

    cls, _model = mock_model_cls
    stt = FasterWhisperSTT()
    await stt.transcribe(b"a")
    await stt.transcribe(b"b")
    assert cls.call_count == 1


@pytest.mark.asyncio
async def test_transcribe_returns_none_on_error(mock_model_cls):
    from planner.infra.stt.faster_whisper import FasterWhisperSTT

    _cls, model = mock_model_cls
    model.transcribe.side_effect = RuntimeError("decode failed")
    stt = FasterWhisperSTT()
    result = await stt.transcribe(b"audio", "voice.ogg")
    assert result is None


@pytest.mark.asyncio
async def test_transcribe_default_filename(mock_model_cls):
    from planner.infra.stt.faster_whisper import FasterWhisperSTT

    stt = FasterWhisperSTT()
    result = await stt.transcribe(b"audio")
    assert result == "привет мир"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_faster_whisper_stt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'planner.infra.stt.faster_whisper'`.

- [ ] **Step 3: Write the adapter**

Create `src/planner/infra/stt/faster_whisper.py`:

```python
"""Local faster-whisper STT adapter (spec section 2 / 4.7).

Runs the Whisper ``small`` model on CPU (int8) — no API key required.
The model is lazy-loaded on first use and cached. The blocking transcription
runs in a worker thread so the event loop is not blocked. On any failure
returns ``None`` so the bot degrades to text-only (spec section 15).
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_MODEL_SIZE = "small"


class FasterWhisperSTT:
    def __init__(self, model_size: str = _MODEL_SIZE) -> None:
        self._model_size = model_size
        self._model: Any = None  # lazy-loaded on first transcribe

    def _load_model(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size, device="cpu", compute_type="int8"
            )
        return self._model

    def _transcribe_sync(self, audio: bytes) -> str:
        model = self._load_model()
        segments, _info = model.transcribe(io.BytesIO(audio), language="ru")
        return "".join(segment.text for segment in segments).strip()

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str | None:
        try:
            return await asyncio.to_thread(self._transcribe_sync, audio)
        except Exception as exc:  # noqa: BLE001 — degrade to text-only
            log.warning("faster_whisper_failed", error=str(exc))
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_faster_whisper_stt.py -v`
Expected: PASS — 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_faster_whisper_stt.py src/planner/infra/stt/faster_whisper.py
git commit -m "feat: add FasterWhisperSTT local adapter"
```

---

## Task 4: Wire FasterWhisperSTT in runner, delete old adapter

**Files:**
- Modify: `src/planner/bot/runner.py:54-56`
- Modify: `tests/unit/bot/test_runner.py:14-23` (helper) and `:89-102` (tests)
- Delete: `src/planner/infra/stt/whisper.py`, `tests/unit/test_whisper_stt.py`

- [ ] **Step 1: Update the runner test (failing first)**

In `tests/unit/bot/test_runner.py`, remove `openai_api_key="",` from the `_settings` helper `base` dict (lines 14-23 area), so it reads:

```python
def _settings(**overrides) -> Settings:
    base = dict(
        database_url="x",
        redis_url="redis://localhost:6380/0",
        bot_token="123:TEST",
        team_chat_id=1,
        anthropic_api_key="",
        jwt_secret="s",
        admin_ids="",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]
```

Then replace the two tests `test_build_dispatcher_openai_key_wires_stt` and `test_build_dispatcher_no_openai_no_stt` with one:

```python
def test_build_dispatcher_always_wires_stt(mock_redis_storage):
    settings = _settings()
    parser = BasicIntentParser()
    dp = build_dispatcher(settings, parser)
    assert "stt" in dp.workflow_data
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/bot/test_runner.py::test_build_dispatcher_always_wires_stt -v`
Expected: FAIL — runner still gates on `settings.openai_api_key` (now absent) / imports `WhisperSTT`.

- [ ] **Step 3: Update the runner wiring**

In `src/planner/bot/runner.py`, replace lines 54-56:

```python
    if settings.openai_api_key:
        from planner.infra.stt.whisper import WhisperSTT
        dp["stt"] = WhisperSTT(settings.openai_api_key)
```
with:
```python
    from planner.infra.stt.faster_whisper import FasterWhisperSTT
    dp["stt"] = FasterWhisperSTT()
```

- [ ] **Step 4: Delete the old adapter and its test**

Run:
```bash
git rm src/planner/infra/stt/whisper.py tests/unit/test_whisper_stt.py
```

- [ ] **Step 5: Run the runner tests**

Run: `uv run pytest tests/unit/bot/test_runner.py -v`
Expected: PASS (all runner tests, including `test_build_dispatcher_always_wires_stt`).

- [ ] **Step 6: Commit**

```bash
git add src/planner/bot/runner.py tests/unit/bot/test_runner.py
git commit -m "feat: wire FasterWhisperSTT unconditionally, remove OpenAI adapter"
```

---

## Task 5: Remove openai_api_key from settings + startup log

**Files:**
- Modify: `src/planner/settings.py:32-34`
- Modify: `src/planner/main.py:51`
- Modify: `tests/unit/test_settings.py` (remove openai-optional test)

- [ ] **Step 1: Remove the settings field**

In `src/planner/settings.py`, delete these three lines (the `openai_api_key` field and its docstring under `# LLM`):

```python
    openai_api_key: str = ""
    """OpenAI API key for Whisper STT (optional — voice messages disabled without it)"""
```

(Keep the `anthropic_api_key` field above it.)

- [ ] **Step 2: Update the startup log**

In `src/planner/main.py`, change line 51:

```python
        stt="whisper" if settings.openai_api_key else "off",
```
to:
```python
        stt="faster-whisper",
```

- [ ] **Step 3: Remove the obsolete settings test**

In `tests/unit/test_settings.py`, delete the whole `test_settings_missing_openai_api_key_defaults_to_empty` method (and its `monkeypatch.delenv("OPENAI_API_KEY", ...)` / `assert s.openai_api_key == ""` body). In any remaining test that asserts on `openai_api_key`, drop that assertion; leave `monkeypatch.setenv/delenv("OPENAI_API_KEY", ...)` lines harmlessly or remove them.

- [ ] **Step 4: Run settings + main import checks**

Run:
```bash
uv run pytest tests/unit/test_settings.py -v && uv run python -c "import planner.main; print('main ok')"
```
Expected: settings tests PASS; `main ok`.

- [ ] **Step 5: Commit**

```bash
git add src/planner/settings.py src/planner/main.py tests/unit/test_settings.py
git commit -m "refactor: drop openai_api_key setting, log stt=faster-whisper"
```

---

## Task 6: Remove OPENAI_API_KEY from env files

**Files:**
- Modify: `.env.example`
- Modify: `.env`

- [ ] **Step 1: Edit `.env.example`**

Remove these two lines:
```
# OpenAI API key for GPT models
OPENAI_API_KEY=sk-your_key_here
```

- [ ] **Step 2: Edit `.env`**

Remove the line:
```
OPENAI_API_KEY=
```

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "chore: drop OPENAI_API_KEY from env example"
```

(`.env` is gitignored — local edit only, not committed.)

---

## Task 7: Full verification

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests pass. No references to `WhisperSTT` / `openai_api_key` remain.

- [ ] **Step 2: Lint + type check**

Run: `uv run ruff check src tests && uv run mypy src`
Expected: clean (no new errors).

- [ ] **Step 3: Grep for orphan references**

Run: `grep -rn "openai_api_key\|WhisperSTT\|infra.stt.whisper\|OPENAI_API_KEY" src tests`
Expected: no matches.

- [ ] **Step 4: Coverage check**

Run: `uv run pytest --cov=planner --cov-report=term-missing -q`
Expected: total coverage ≥ 80%.

---

## Notes for the implementer

- **First real run downloads ~480 MB** from HuggingFace to `~/.cache/huggingface` (network once, offline after). Tests mock `faster_whisper.WhisperModel`, so the suite never downloads.
- The adapter import of `WhisperModel` is **inside** `_load_model` on purpose — keeps `import planner.bot.runner` cheap and lets `FasterWhisperSTT()` construct with no model load (so `build_dispatcher` stays fast).
- Do not change the bot voice handler (`task_router.py handle_voice`) or `STTPort` signature — the swap is adapter-only by design.
