"""Dispatcher assembly and polling entrypoint (spec section 8 / Sprint 3.1)."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand

from planner.app.confirm_plan import ConfirmPlanUseCase
from planner.app.explain_plan import ExplainPlanUseCase
from planner.app.ports import RepoPort
from planner.bot.handlers import (
    clarify,
    confirm,
    load,
    replan,
    start,
    suggest,
    task_router,
    vacation,
    whatif,
)
from planner.bot.middlewares.errors import ErrorBoundaryMiddleware
from planner.bot.middlewares.permissions import ActorMiddleware
from planner.bot.middlewares.throttle import ThrottleMiddleware
from planner.domain.solver.ports import SolverPort
from planner.infra.llm.basic import BasicIntentParser
from planner.infra.llm.ports import IntentParserPort
from planner.settings import Settings

# Telegram command menu (spec section 8). Kept in one place so /-handlers and
# the menu can't drift apart.
BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Привет и краткая справка"),
    ("task", "Новая задача или проект"),
    ("load", "Загрузка команды на 14 дней"),
    ("whatif", "Сценарий «что-если» (без записи)"),
    ("vacation", "Оформить отпуск / выходной"),
    ("suggest", "Кто может взять задачу по скиллам"),
    ("replan", "Пересчитать план по текущим данным"),
)


async def register_bot_commands(bot: Bot) -> None:
    """Publish the slash-command menu to Telegram (spec section 8)."""
    await bot.set_my_commands(
        [BotCommand(command=cmd, description=desc) for cmd, desc in BOT_COMMANDS]
    )


def build_parser(settings: Settings) -> IntentParserPort:
    """Claude when a key is configured, regex fallback otherwise (spec 15)."""
    if settings.anthropic_api_key:
        from planner.infra.llm.claude import ClaudeIntentParser

        return ClaudeIntentParser(settings.anthropic_api_key)
    return BasicIntentParser()


def build_dispatcher(
    settings: Settings,
    parser: IntentParserPort,
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
) -> Dispatcher:
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)
    dp["parser"] = parser
    # Tool-use agent (singleton). ToolBox is request-scoped — built per message in
    # the handler with the request actor — so only the agent lives on the dispatcher.
    if settings.anthropic_api_key and settings.agent_enabled:
        from planner.infra.llm.agent import PlannerAgent

        dp["agent"] = PlannerAgent(settings.anthropic_api_key)
    if repo is not None:
        dp["repo"] = repo
    if solver is not None:
        dp["solver"] = solver
    dp["explain_uc"] = ExplainPlanUseCase(None)
    if repo is not None:
        dp["confirm_uc"] = ConfirmPlanUseCase(repo)
    from planner.infra.stt.faster_whisper import FasterWhisperSTT
    dp["stt"] = FasterWhisperSTT()

    from planner.infra.history import ChatHistory
    dp["history"] = ChatHistory()

    from planner.infra.notion.client import NotionTaskSink, NullTaskSink
    sink = (
        NotionTaskSink(settings.notion_token, settings.notion_database_id)
        if settings.notion_token and settings.notion_database_id
        else NullTaskSink()
    )
    dp["task_sink"] = sink

    errors_mw = ErrorBoundaryMiddleware()
    dp.message.middleware(errors_mw)
    dp.callback_query.middleware(errors_mw)

    throttle_mw = ThrottleMiddleware()
    dp.message.middleware(throttle_mw)

    actor_mw = ActorMiddleware(settings.admin_id_set, repo)
    dp.message.middleware(actor_mw)
    dp.callback_query.middleware(actor_mw)

    dp.include_router(start.router)
    # Before task_router: its state-filtered clarify text handlers must preempt
    # task_router's catch-all text handler.
    dp.include_router(clarify.router)
    dp.include_router(task_router.router)
    dp.include_router(load.router)
    dp.include_router(suggest.router)
    dp.include_router(whatif.router)
    dp.include_router(confirm.router)
    dp.include_router(vacation.router)
    dp.include_router(replan.router)
    return dp


async def run(settings: Settings) -> None:
    bot = Bot(token=settings.bot_token)
    parser = build_parser(settings)
    dp = build_dispatcher(settings, parser)
    await dp.start_polling(bot)
