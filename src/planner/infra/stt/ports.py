"""Speech-to-text port (spec section 2 / 4.7)."""

from __future__ import annotations

from typing import Protocol


class STTPort(Protocol):
    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str | None: ...
