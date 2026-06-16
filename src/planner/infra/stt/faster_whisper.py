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

# Biases the decoder toward team names and presales jargon the base model
# otherwise mangles ("ресёрч"→"ресурс", "Рай"→"Ирай"). Tune as the team changes.
_INITIAL_PROMPT = (
    "Планирование задач команды пресейла. "
    "Имена: Андрей, Рай, Айгуль, Лёша, Катя, Дима. "
    "Термины: бриф, ресёрч, КП, дедлайн, оффер, лид, пресейл, МТС, Мегафон."
)


class FasterWhisperSTT:
    def __init__(
        self, model_size: str = _MODEL_SIZE, initial_prompt: str | None = _INITIAL_PROMPT
    ) -> None:
        self._model_size = model_size
        self._initial_prompt = initial_prompt
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
        segments, _info = model.transcribe(
            io.BytesIO(audio), language="ru", initial_prompt=self._initial_prompt
        )
        return "".join(segment.text for segment in segments).strip()

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str | None:
        try:
            return await asyncio.to_thread(self._transcribe_sync, audio)
        except Exception as exc:  # noqa: BLE001 — degrade to text-only
            log.warning("faster_whisper_failed", error=str(exc))
            return None
