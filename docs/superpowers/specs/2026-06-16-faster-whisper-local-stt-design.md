# Local faster-whisper STT (replace OpenAI Whisper)

**Date:** 2026-06-16
**Status:** Approved — ready for implementation plan

## Goal

Transcribe Telegram voice messages with a free local model (`faster-whisper`)
instead of the cloud OpenAI Whisper API. No API key required.

## Decisions

- **Replace OpenAI fully.** Delete the `WhisperSTT` adapter and the `openai`
  dependency. `faster-whisper` becomes the only STT backend.
- **Model: `small`** (`device="cpu"`, `compute_type="int8"`). Good Russian
  accuracy for short task commands; few seconds per clip on CPU. Hardcoded, not
  configurable (YAGNI — can be lifted later).
- **Always-on, no disable switch.** Local STT needs no key, so STT is always
  wired. No off-toggle.
- **Lazy model load.** Model loads on first `transcribe` and is cached. No
  startup cost. First-ever run downloads ~480 MB from HuggingFace to
  `~/.cache/huggingface` (network once, offline after).

## Architecture

The existing `STTPort` protocol remains the contract. A new adapter,
`FasterWhisperSTT`, satisfies it. The bot voice handler and all downstream
routing are **unchanged** — only the adapter behind the port swaps.

### Adapter: `FasterWhisperSTT`

- Lazy-loads `WhisperModel("small", device="cpu", compute_type="int8")` on first
  `transcribe`, caches the instance.
- `transcribe(audio: bytes, filename: str = "voice.ogg") -> str | None`:
  - Wrap bytes in `io.BytesIO` → `model.transcribe(buf, language="ru")`.
  - Join segment texts, strip whitespace, return.
  - `filename` is accepted for port compatibility but unused (faster-whisper
    decodes by content).
- faster-whisper decodes ogg/opus itself via bundled PyAV — **no system ffmpeg
  needed**.
- CPU-bound + synchronous → the blocking work runs inside `asyncio.to_thread`
  so the event loop is not blocked.
- Any exception (load failure, decode failure, download failure) → log a
  warning and return `None`. This preserves the existing degradation contract:
  the handler replies "Не удалось распознать голос — напиши текстом."
- VAD filter left off (default) — short clips, keeps behavior simple.

## Files

| File | Change |
|---|---|
| `src/planner/infra/stt/ports.py` | NEW — move `STTPort` here (matches `infra/llm/ports.py` convention) |
| `src/planner/infra/stt/faster_whisper.py` | NEW — `FasterWhisperSTT` |
| `src/planner/infra/stt/whisper.py` | DELETE |
| `src/planner/bot/runner.py` | wire `dp["stt"] = FasterWhisperSTT()` always (drop `openai_api_key` gate); import update |
| `src/planner/bot/handlers/task_router.py` | import `STTPort` from `planner.infra.stt.ports` |
| `src/planner/settings.py` | remove `openai_api_key` field |
| `src/planner/main.py` | startup log `stt="faster-whisper"` |
| `pyproject.toml` | remove `openai`, add `faster-whisper` |
| `.env.example`, `.env` | drop `OPENAI_API_KEY` |

### `openai` package removal — contingent

Only `whisper.py` imports `openai`. `instructor` (used for Claude intent
parsing) *might* import `openai` transitively. Plan: remove the `openai` dep,
then run an import smoke test (`python -c "import planner.main"` /
`import instructor`). If instructor breaks, keep the `openai` dep but still
delete the adapter code and settings field.

## Data flow (unchanged downstream)

```
voice message
  → handle_voice (task_router.py): size check, download ogg bytes
  → FasterWhisperSTT.transcribe(bytes) -> text | None
  → _handle_text(text, ...) : existing intent routing
```

If `transcribe` returns `None` or empty → handler replies with the
text-fallback hint. No downstream changes.

## Error handling

- Model load / decode / download exception → log `warning`, return `None`.
- Empty transcription → handler treats as failure (existing `if not text`).
- First-run HuggingFace download requires network once; failure degrades to
  text-only.

## Testing (TDD)

- `tests/unit/test_faster_whisper_stt.py` (replaces `test_whisper_stt.py`):
  - mock `faster_whisper.WhisperModel`; segments join into returned text;
  - exception in `transcribe` → returns `None`;
  - model loaded once and cached across calls;
  - blocking call offloaded via `asyncio.to_thread`.
- `tests/unit/bot/test_runner.py`: replace the two openai-key-gated tests with
  one asserting `stt` is always wired.
- `tests/unit/test_settings.py`: drop the `openai_api_key`-optional test.
- `tests/unit/bot/test_handler_coverage.py`: unchanged — voice tests pass `stt`
  explicitly.

Target: keep ≥ 80% coverage, all existing tests green.
