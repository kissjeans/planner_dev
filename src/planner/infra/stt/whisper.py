"""OpenAI Whisper STT adapter (spec section 2 / 4.7).

On any failure returns ``None`` so the bot can ask the user to retype
(spec section 15 degradation).
"""

from __future__ import annotations

from typing import Protocol

import structlog

log = structlog.get_logger(__name__)


class STTPort(Protocol):
    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str | None: ...


class WhisperSTT:
    def __init__(self, api_key: str, model: str = "whisper-1") -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str | None:
        try:
            resp = await self._client.audio.transcriptions.create(
                model=self._model,
                file=(filename, audio),
                language="ru",
            )
            return resp.text
        except Exception as exc:  # noqa: BLE001 — degrade to text-only
            log.warning("whisper_failed", error=str(exc))
            return None
