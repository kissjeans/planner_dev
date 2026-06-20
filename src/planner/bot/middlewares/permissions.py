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

    def __init__(
        self,
        admin_ids: set[int],
        repo: RepoPort | None = None,
        team_chat_id: int | None = None,
    ) -> None:
        self._admin_ids = admin_ids
        self._repo = repo
        self._team_chat_id = team_chat_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        tg_id = user.id if user else None

        # C5: membership in the team chat = full rights (single-tenant; no
        # read-only tier). A message coming from the team chat, or a sender who
        # is a known team member (has a Person record), is authorized to write.
        chat = data.get("event_chat")
        chat_id = getattr(chat, "id", None)
        in_team_chat = self._team_chat_id is not None and chat_id == self._team_chat_id

        is_admin = tg_id in self._admin_ids if tg_id is not None else False

        if self._repo is not None and tg_id is not None:
            record = await self._repo.get_person_by_tg_id(tg_id)
            if record is not None:
                data["actor_record"] = record
                is_admin = True  # known team member = full rights (C5)

        is_admin = is_admin or in_team_chat

        data["actor"] = {
            "tg_user_id": tg_id,
            "is_admin": is_admin,
            "in_team_chat": in_team_chat,
        }
        return await handler(event, data)
