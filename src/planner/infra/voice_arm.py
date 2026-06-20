"""Per-(chat, user) "voice armed" flag.

In a group the bot ignores voices unless addressed. Telegram voices carry no
caption, so the practical way to address one is: @mention the bot in a text
message, then send the voice. An @mention arms that user's next voice for a
short window; the voice handler checks the flag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_WINDOW = timedelta(seconds=120)


class VoiceArm:
    """In-memory store of recent bot @mentions per (chat, user)."""

    def __init__(self) -> None:
        self._armed: dict[tuple[int, int], datetime] = {}

    def arm(self, chat_id: int, user_id: int, *, now: datetime | None = None) -> None:
        self._armed[(chat_id, user_id)] = now or datetime.now(UTC)

    def is_armed(
        self, chat_id: int, user_id: int, *, now: datetime | None = None
    ) -> bool:
        ts = self._armed.get((chat_id, user_id))
        if ts is None:
            return False
        return (now or datetime.now(UTC)) - ts <= _WINDOW
