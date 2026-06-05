"""Tests for APScheduler job registration (spec section 11)."""

from __future__ import annotations

from typing import Any

import pytest

from planner.infra.scheduler import SchedulerDeps, register_jobs


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def add_job(self, func: Any, trigger: Any, id: str = "", replace_existing: bool = False) -> None:
        self.jobs.append({"func": func, "trigger": trigger, "id": id})


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


async def _noop() -> None:
    pass
