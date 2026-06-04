"""APScheduler jobs: daily load summary + yearly calendar refresh (spec 11)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Awaitable


@dataclass
class SchedulerDeps:
    send_daily_summary: Callable[[], Awaitable[None]]
    refresh_calendar_snapshot: Callable[[], Awaitable[None]]
    timezone: str = "Europe/Moscow"


def register_jobs(scheduler: Any, deps: SchedulerDeps) -> None:
    """Attach the recurring jobs to an APScheduler instance."""
    from apscheduler.triggers.cron import CronTrigger

    scheduler.add_job(
        deps.send_daily_summary,
        CronTrigger(
            day_of_week="mon-fri", hour=9, minute=30, timezone=deps.timezone
        ),
        id="daily_load_summary",
        replace_existing=True,
    )
    scheduler.add_job(
        deps.refresh_calendar_snapshot,
        CronTrigger(month=1, day=1, timezone=deps.timezone),
        id="refresh_calendar_snapshot",
        replace_existing=True,
    )
