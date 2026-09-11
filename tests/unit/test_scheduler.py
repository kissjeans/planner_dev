"""Tests for APScheduler job registration (spec section 11)."""

from __future__ import annotations

from typing import Any

import pytest

from planner.infra.scheduler import SchedulerDeps, register_jobs


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.listeners: list[Any] = []

    def add_job(
        self, func: Any, trigger: Any, id: str = "",
        replace_existing: bool = False, **kwargs: Any,
    ) -> None:
        self.jobs.append({"func": func, "trigger": trigger, "id": id, **kwargs})

    def add_listener(self, callback: Any, mask: Any = None) -> None:
        self.listeners.append(callback)


@pytest.mark.asyncio
async def test_register_jobs_adds_two_jobs():
    sched = _FakeScheduler()
    deps = SchedulerDeps(
        send_daily_summary=_noop,
        refresh_calendar_snapshot=_noop,
        timezone="Europe/Moscow",
    )
    register_jobs(sched, deps)
    assert len(sched.jobs) == 2


@pytest.mark.asyncio
async def test_register_jobs_ids():
    sched = _FakeScheduler()
    deps = SchedulerDeps(
        send_daily_summary=_noop,
        refresh_calendar_snapshot=_noop,
    )
    register_jobs(sched, deps)
    ids = {j["id"] for j in sched.jobs}
    assert "daily_load_summary" in ids
    assert "refresh_calendar_snapshot" in ids


@pytest.mark.asyncio
async def test_register_jobs_functions_match_deps():
    sched = _FakeScheduler()
    deps = SchedulerDeps(
        send_daily_summary=_noop,
        refresh_calendar_snapshot=_noop,
    )
    register_jobs(sched, deps)
    funcs = {j["func"] for j in sched.jobs}
    assert _noop in funcs


@pytest.mark.asyncio
async def test_daily_summary_fires_at_10_00_moscow_on_weekdays():
    """R7: daily summary at 10:00 Europe/Moscow, weekdays."""
    sched = _FakeScheduler()
    register_jobs(
        sched,
        SchedulerDeps(
            send_daily_summary=_noop,
            refresh_calendar_snapshot=_noop,
            timezone="Europe/Moscow",
        ),
    )
    daily = next(j for j in sched.jobs if j["id"] == "daily_load_summary")
    trigger = str(daily["trigger"])
    assert "hour='10'" in trigger
    assert "minute='0'" in trigger
    assert "day_of_week='mon-fri'" in trigger


@pytest.mark.asyncio
async def test_register_jobs_adds_error_listener_and_misfire():
    sched = _FakeScheduler()
    register_jobs(
        sched, SchedulerDeps(send_daily_summary=_noop, refresh_calendar_snapshot=_noop)
    )
    assert sched.listeners  # EVENT_JOB_ERROR listener attached
    daily = next(j for j in sched.jobs if j["id"] == "daily_load_summary")
    assert daily.get("misfire_grace_time")
    assert daily.get("coalesce")


def test_on_job_error_logs_without_raising():
    from planner.infra.scheduler import _on_job_error

    event = type("E", (), {"job_id": "x", "exception": ValueError("boom")})()
    _on_job_error(event)  # must not raise


async def _noop() -> None:
    pass


@pytest.mark.asyncio
async def test_daily_summary_can_be_switched_off():
    """The team asked to mute the 10:00 load digest while the bot is reworked.

    Only the digest goes away — the calendar refresh must keep running.
    """
    sched = _FakeScheduler()
    deps = SchedulerDeps(
        send_daily_summary=_noop,
        refresh_calendar_snapshot=_noop,
        timezone="Europe/Moscow",
        daily_summary_enabled=False,
    )

    register_jobs(sched, deps)

    assert [j["id"] for j in sched.jobs] == ["refresh_calendar_snapshot"]


@pytest.mark.asyncio
async def test_daily_summary_is_on_by_default():
    sched = _FakeScheduler()
    deps = SchedulerDeps(send_daily_summary=_noop, refresh_calendar_snapshot=_noop)

    register_jobs(sched, deps)

    assert "daily_load_summary" in {j["id"] for j in sched.jobs}
