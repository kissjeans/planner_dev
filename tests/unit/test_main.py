"""Coverage test for the entrypoint (main.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run_main_mocked():
    """Run main() fully mocked; return (fake_dp, fake_server, captured_deps)."""

    fake_settings = MagicMock()
    fake_settings.bot_token = "123:TEST"
    fake_settings.team_chat_id = -1
    fake_settings.timezone = "Europe/Moscow"
    fake_dp = MagicMock()
    fake_dp.start_polling = AsyncMock()
    fake_dp.workflow_data.get.return_value = None  # no stt → skip warmup task
    fake_server = MagicMock()
    fake_server.serve = AsyncMock()
    fake_scheduler = MagicMock()
    captured: list = []

    def _capture_jobs(_sched, deps):
        captured.append(deps)

    return fake_settings, fake_dp, fake_server, fake_scheduler, captured, _capture_jobs


@pytest.mark.asyncio
async def test_main_wires_and_runs():
    """Smoke-test main() — all IO mocked, scheduler started, polling called."""
    import planner.main as main_mod
    fake_settings, fake_dp, fake_server, fake_scheduler, captured, capture_jobs = (
        _run_main_mocked()
    )

    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    fake_bot = MagicMock()
    fake_bot.session.close = AsyncMock()
    fake_bot.set_my_commands = AsyncMock()
    with (
        patch.object(main_mod, "get_settings", return_value=fake_settings),
        patch.object(main_mod, "create_engine", return_value=fake_engine),
        patch.object(main_mod, "create_session_factory", return_value=MagicMock()),
        patch.object(main_mod, "SqlAlchemyRepo", return_value=MagicMock()),
        patch.object(main_mod, "Bot", return_value=fake_bot),
        patch.object(main_mod, "GreedySolver", return_value=MagicMock()),
        patch.object(main_mod, "SnapshotCalendar", return_value=MagicMock()),
        patch.object(main_mod, "_load_calendar", new=AsyncMock(return_value=MagicMock())),
        patch.object(main_mod, "build_parser", return_value=MagicMock()),
        patch.object(main_mod, "build_dispatcher", return_value=fake_dp),
        patch.object(main_mod, "create_app", return_value=MagicMock()),
        patch("uvicorn.Server", return_value=fake_server),
        patch.object(main_mod, "AsyncIOScheduler", return_value=fake_scheduler),
        patch.object(main_mod, "register_jobs", side_effect=capture_jobs),
    ):
        await main_mod.main()

    assert fake_bot.set_my_commands.called
    assert fake_dp.start_polling.called
    assert fake_server.serve.called
    assert fake_scheduler.start.called
    assert captured  # register_jobs was called with SchedulerDeps


@pytest.mark.asyncio
async def test_daily_summary_with_png():
    """main.py:45-51 — _daily_summary sends photo when PNG available."""
    import planner.main as main_mod
    fake_settings, fake_dp, fake_server, fake_scheduler, captured, capture_jobs = (
        _run_main_mocked()
    )
    fake_bot = MagicMock()
    fake_bot.send_photo = AsyncMock()
    fake_bot.send_message = AsyncMock()
    fake_bot.session.close = AsyncMock()
    fake_bot.set_my_commands = AsyncMock()
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()

    fake_calendar_sentinel = MagicMock()
    fake_solver = MagicMock()

    with (
        patch.object(main_mod, "get_settings", return_value=fake_settings),
        patch.object(main_mod, "create_engine", return_value=fake_engine),
        patch.object(main_mod, "create_session_factory", return_value=MagicMock()),
        patch.object(main_mod, "SqlAlchemyRepo", return_value=MagicMock()),
        patch.object(main_mod, "Bot", return_value=fake_bot),
        patch.object(main_mod, "GreedySolver", return_value=fake_solver),
        patch.object(main_mod, "SnapshotCalendar", return_value=MagicMock()),
        patch.object(
            main_mod, "_load_calendar", new=AsyncMock(return_value=fake_calendar_sentinel)
        ),
        patch.object(main_mod, "build_parser", return_value=MagicMock()),
        patch.object(main_mod, "build_dispatcher", return_value=fake_dp),
        patch.object(main_mod, "create_app", return_value=MagicMock()),
        patch("uvicorn.Server", return_value=fake_server),
        patch.object(main_mod, "AsyncIOScheduler", return_value=fake_scheduler),
        patch.object(main_mod, "register_jobs", side_effect=capture_jobs),
        patch.object(main_mod, "build_load_image", new=AsyncMock(return_value=b"PNG")),
    ):
        await main_mod.main()
        deps = captured[0]
        await deps.send_daily_summary()
        assert fake_bot.send_photo.called

        # refresh job swaps solver.calendar with the sentinel (patch still active)
        await deps.refresh_calendar_snapshot()
        assert fake_solver.calendar == fake_calendar_sentinel

    # no-PNG path — re-run with None result
    with patch.object(main_mod, "build_load_image", new=AsyncMock(return_value=None)):
        await deps.send_daily_summary()
    assert fake_bot.send_message.called


@pytest.mark.asyncio
async def test_load_calendar_falls_back_on_network_error():
    import planner.main as main_mod

    with patch.object(
        main_mod,
        "fetch_snapshot_for_years",
        new=AsyncMock(side_effect=RuntimeError("net down")),
    ):
        cal = await main_mod._load_calendar()
    from planner.infra.calendar.snapshot import SnapshotCalendar

    assert isinstance(cal, SnapshotCalendar)
