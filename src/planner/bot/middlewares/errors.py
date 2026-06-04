"""Error-boundary middleware (spec section 6.2 / 6.1).

Binds a correlation id, lets the handler run, and on any exception logs the
full traceback server-side while sending only a friendly message to the user.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from planner.app.errors import user_message
from planner.infra.logging import new_correlation_id

log = structlog.get_logger(__name__)


class ErrorBoundaryMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        new_correlation_id()
        try:
            return await handler(event, data)
        except Exception as exc:  # noqa: BLE001 — boundary: nothing escapes to user
            log.exception("handler_failed", error=str(exc))
            text = user_message(exc)
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            return None
