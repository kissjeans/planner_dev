"""Dispatcher assembly and polling entrypoint (spec section 8 / Sprint 3.1)."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage

from planner.bot.handlers import confirm, load, start, task_router, whatif
from planner.bot.middlewares.errors import ErrorBoundaryMiddleware
from planner.bot.middlewares.permissions import ActorMiddleware
from planner.infra.llm.basic import BasicIntentParser
from planner.infra.llm.ports import IntentParserPort
from planner.settings import Settings


def build_parser(settings: Settings) -> IntentParserPort:
    """Claude when a key is configured, regex fallback otherwise (spec 15)."""
    if settings.anthropic_api_key:
        from planner.infra.llm.claude import ClaudeIntentParser

        return ClaudeIntentParser(settings.anthropic_api_key)
    return BasicIntentParser()


def build_dispatcher(settings: Settings, parser: IntentParserPort) -> Dispatcher:
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)
    dp["parser"] = parser

    errors_mw = ErrorBoundaryMiddleware()
    dp.message.middleware(errors_mw)
    dp.callback_query.middleware(errors_mw)

    actor_mw = ActorMiddleware(settings.admin_id_set)
    dp.message.middleware(actor_mw)
    dp.callback_query.middleware(actor_mw)

    dp.include_router(start.router)
    dp.include_router(task_router.router)
    dp.include_router(load.router)
    dp.include_router(whatif.router)
    dp.include_router(confirm.router)
    return dp


async def run(settings: Settings) -> None:
    bot = Bot(token=settings.bot_token)
    parser = build_parser(settings)
    dp = build_dispatcher(settings, parser)
    await dp.start_polling(bot)
