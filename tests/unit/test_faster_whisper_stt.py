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


@pytest.mark.asyncio
async def test_transcribe_passes_initial_prompt(mock_model_cls):
    from planner.infra.stt.faster_whisper import _INITIAL_PROMPT, FasterWhisperSTT

    _cls, model = mock_model_cls
    stt = FasterWhisperSTT()
    await stt.transcribe(b"data")
    kwargs = model.transcribe.call_args.kwargs
    assert kwargs["initial_prompt"] == _INITIAL_PROMPT
    assert "Рай" in _INITIAL_PROMPT and "бриф" in _INITIAL_PROMPT


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
