"""Actor-resolution middleware (spec section 16).

Resolves the Telegram sender into an ``actor`` dict carrying ``is_admin`` and
injects it into handler data. The actual write-gate is applied in the handler
via :func:`planner.domain.permissions.can_execute`, because the intent is only
known after parsing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class ActorMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: set[int]) -> None:
        self._admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        tg_id = user.id if user else None
        data["actor"] = {
            "tg_user_id": tg_id,
            "is_admin": tg_id in self._admin_ids if tg_id is not None else False,
        }
        return await handler(event, data)
