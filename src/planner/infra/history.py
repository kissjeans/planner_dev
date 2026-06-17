"""In-process per-chat message history for short-term reference resolution.

Intentionally ephemeral: the bot is single-process (spec §17), so history
lives only in memory and is lost on restart. No persistence by design.
"""

from __future__ import annotations

from collections import deque


class ChatHistory:
    """Bounded per-chat ring buffer of recent message texts."""

    def __init__(self, max_turns: int = 8) -> None:
        self._max_turns = max_turns
        self._by_chat: dict[int, deque[str]] = {}

    def record(self, chat_id: int, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        buf = self._by_chat.get(chat_id)
        if buf is None:
            buf = self._by_chat[chat_id] = deque(maxlen=self._max_turns)
        buf.append(stripped)

    def recent(self, chat_id: int) -> tuple[str, ...]:
        return tuple(self._by_chat.get(chat_id, ()))
