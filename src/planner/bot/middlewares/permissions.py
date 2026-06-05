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

from planner.app.ports import RepoPort


class ActorMiddleware(BaseMiddleware):
    """Resolves the sender into an ``actor`` dict and, if a repo is wired, the
    matching ``actor_record`` (``PersonRecord``) so write use-cases get a real id."""

    def __init__(self, admin_ids: set[int], repo: RepoPort | None = None) -> None:
        self._admin_ids = admin_ids
        self._repo = repo

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        tg_id = user.id if user else None
        is_admin = tg_id in self._admin_ids if tg_id is not None else False

        if self._repo is not None and tg_id is not None:
            record = await self._repo.get_person_by_tg_id(tg_id)
            if record is not None:
                data["actor_record"] = record
                is_admin = is_admin or record.is_admin

        data["actor"] = {"tg_user_id": tg_id, "is_admin": is_admin}
        return await handler(event, data)
