"""Tests for ThrottleMiddleware per-user rate-limiting."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from planner.bot.middlewares.throttle import ThrottleMiddleware


async def _run(mw, tg_id):
    calls = {"n": 0}

    async def handler(event, data):
        calls["n"] += 1
        return "ok"

    data = {"event_from_user": SimpleNamespace(id=tg_id) if tg_id else None}
    result = await mw(handler, object(), data)
    return result, calls["n"]


@pytest.mark.asyncio
async def test_first_message_passes():
    mw = ThrottleMiddleware(min_interval_s=10.0)
    result, n = await _run(mw, 42)
    assert result == "ok" and n == 1


@pytest.mark.asyncio
async def test_second_message_within_interval_dropped():
    mw = ThrottleMiddleware(min_interval_s=10.0)
    await _run(mw, 42)
    result, n = await _run(mw, 42)  # immediately again
    assert result is None and n == 0  # handler not called the second time


@pytest.mark.asyncio
async def test_different_users_not_throttled_together():
    mw = ThrottleMiddleware(min_interval_s=10.0)
    await _run(mw, 1)
    result, n = await _run(mw, 2)
    assert result == "ok" and n == 1


@pytest.mark.asyncio
async def test_event_without_user_passes():
    mw = ThrottleMiddleware(min_interval_s=10.0)
    result, n = await _run(mw, None)
    assert result == "ok" and n == 1


@pytest.mark.asyncio
async def test_message_after_interval_passes():
    mw = ThrottleMiddleware(min_interval_s=0.0)  # no throttling window
    await _run(mw, 42)
    result, n = await _run(mw, 42)
    assert result == "ok" and n == 1
