"""Unit tests for WhisperSTT adapter (spec section 4.7)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def mock_openai(monkeypatch):
    """Patch openai.AsyncOpenAI so no real HTTP calls happen."""
    client = MagicMock()
    client.audio = MagicMock()
    client.audio.transcriptions = MagicMock()
    client.audio.transcriptions.create = AsyncMock(
        return_value=SimpleNamespace(text="распознанный текст")
    )
    with patch("openai.AsyncOpenAI", return_value=client):
        yield client


@pytest.mark.asyncio
async def test_transcribe_returns_text(mock_openai):
    from planner.infra.stt.whisper import WhisperSTT

    stt = WhisperSTT("sk-test")
    result = await stt.transcribe(b"audio_bytes", "voice.ogg")
    assert result == "распознанный текст"


@pytest.mark.asyncio
async def test_transcribe_passes_correct_args(mock_openai):
    from planner.infra.stt.whisper import WhisperSTT

    stt = WhisperSTT("sk-test", model="whisper-1")
    await stt.transcribe(b"data", "clip.ogg")
    mock_openai.audio.transcriptions.create.assert_awaited_once()
    call_kwargs = mock_openai.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["language"] == "ru"
    assert call_kwargs["model"] == "whisper-1"


@pytest.mark.asyncio
async def test_transcribe_returns_none_on_error(mock_openai):
    from planner.infra.stt.whisper import WhisperSTT

    mock_openai.audio.transcriptions.create.side_effect = RuntimeError("api down")
    stt = WhisperSTT("sk-test")
    result = await stt.transcribe(b"audio", "voice.ogg")
    assert result is None


@pytest.mark.asyncio
async def test_transcribe_default_filename(mock_openai):
    from planner.infra.stt.whisper import WhisperSTT

    stt = WhisperSTT("sk-test")
    result = await stt.transcribe(b"audio")
    assert result == "распознанный текст"
