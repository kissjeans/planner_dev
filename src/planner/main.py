"""Single-process entrypoint (spec section 9.3 / 5.7).

Runs the aiogram bot, the FastAPI admin, and the APScheduler jobs in one
asyncio event loop — one process, one DB writer (spec section 17).
"""

from __future__ import annotations

import asyncio
from datetime import date

import structlog
import uvicorn
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from planner.bot.handlers.load import build_load_image
from planner.bot.runner import build_dispatcher, build_parser, register_bot_commands
from planner.domain.solver.greedy import GreedySolver
from planner.infra.calendar.isdayoff import fetch_snapshot_for_years
from planner.infra.calendar.snapshot import SnapshotCalendar
from planner.infra.db.base import create_engine, create_session_factory
from planner.infra.db.repo import SqlAlchemyRepo
from planner.infra.logging import configure_logging
from planner.infra.scheduler import SchedulerDeps, register_jobs
from planner.settings import ensure_secure_config, get_settings
from planner.web.app import create_app

log = structlog.get_logger("planner.main")


async def _load_calendar() -> SnapshotCalendar:
    """Live production calendar when isdayoff.ru is reachable, else snapshot."""
    year = date.today().year
    try:
        return await fetch_snapshot_for_years((year, year + 1))
    except Exception as exc:  # noqa: BLE001 — offline fallback by design (spec 10)
        log.warning("calendar_fetch_failed", error=str(exc))
        return SnapshotCalendar()


async def main() -> None:
    settings = get_settings()
    ensure_secure_config(settings)

    configure_logging(json_logs=not settings.debug, level="DEBUG" if settings.debug else "INFO")
    parser_kind = "claude" if settings.anthropic_api_key else "basic-regex"
    log.info(
        "startup",
        parser=parser_kind,
        stt="faster-whisper",
        admin_ids=sorted(settings.admin_id_set),
        timezone=settings.timezone,
        notion="on"
        if settings.notion_token and settings.notion_database_id
        else "off",
    )

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    repo = SqlAlchemyRepo(session_factory)

    bot = Bot(token=settings.bot_token)
    await register_bot_commands(bot)
    solver = GreedySolver(await _load_calendar())
    dp = build_dispatcher(settings, build_parser(settings), repo, solver)
    stt = dp.workflow_data.get("stt")
    if stt is not None:
        asyncio.create_task(stt.warmup())

    app = create_app(repo, settings)
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    )

    scheduler = AsyncIOScheduler()

    async def _daily_summary() -> None:
        from datetime import date

        from aiogram.types import BufferedInputFile
        try:
            png = await build_load_image(repo, start=date.today())
            if png:
                await bot.send_photo(
                    settings.team_chat_id,
                    BufferedInputFile(png, filename="load.png"),
                    caption="Дневная сводка нагрузки команды.",
                )
            else:
                await bot.send_message(
                    settings.team_chat_id, "Дневная сводка: активных планов нет."
                )
        except Exception:
            log.exception("daily_summary_failed")

    async def _refresh_calendar() -> None:  # snapshot refresh hook (spec 11)
        solver.calendar = await _load_calendar()
        log.info("calendar_refreshed")

    register_jobs(
        scheduler,
        SchedulerDeps(
            send_daily_summary=_daily_summary,
            refresh_calendar_snapshot=_refresh_calendar,
            timezone=settings.timezone,
        ),
    )
    try:
        scheduler.start()
        log.info("running", web="http://0.0.0.0:8000", polling=True)
        await asyncio.gather(dp.start_polling(bot), server.serve())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
