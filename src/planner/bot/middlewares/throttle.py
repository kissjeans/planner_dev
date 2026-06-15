"""Per-user rate-limiting middleware (spec 15 risk: LLM cost / spam).

Single-process, in-memory minimum-interval throttle: messages from the same
Telegram user arriving faster than ``min_interval_s`` are dropped before they
reach the parser. Keyed by Telegram user id; events without a user pass through.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

_MIN_INTERVAL_S = 1.0  # at most one handled message per user per second


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, min_interval_s: float = _MIN_INTERVAL_S) -> None:
        self._min = min_interval_s
        self._last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        tg_id = user.id if user else None
        if tg_id is not None:
            now = time.monotonic()
            last = self._last_seen.get(tg_id)
            if last is not None and now - last < self._min:
                return None  # too soon — drop silently
            self._last_seen[tg_id] = now
        return await handler(event, data)
