"""Tests for ActorMiddleware actor / actor_record resolution (spec 16)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from planner.app.ports import PersonRecord
from planner.bot.middlewares.permissions import ActorMiddleware


class _Repo:
    def __init__(self, by_tg: dict[int, PersonRecord]) -> None:
        self._by_tg = by_tg

    async def get_person_by_tg_id(self, tg_user_id: int) -> PersonRecord | None:
        return self._by_tg.get(tg_user_id)


async def _run(mw, tg_id):
    captured: dict = {}

    async def handler(event, data):
        captured.update(data)
        return "ok"

    data = {"event_from_user": SimpleNamespace(id=tg_id) if tg_id else None}
    await mw(handler, object(), data)
    return captured


@pytest.mark.asyncio
async def test_admin_by_id_set_without_repo():
    captured = await _run(ActorMiddleware({42}), 42)
    assert captured["actor"]["is_admin"] is True
    assert "actor_record" not in captured


@pytest.mark.asyncio
async def test_actor_record_resolved_from_repo():
    rec = PersonRecord(id=uuid4(), name="Андрей", is_admin=True)
    captured = await _run(ActorMiddleware(set(), _Repo({7: rec})), 7)
    assert captured["actor_record"] is rec
    assert captured["actor"]["is_admin"] is True  # promoted by person.is_admin


@pytest.mark.asyncio
async def test_unknown_user_no_record_not_admin():
    captured = await _run(ActorMiddleware(set(), _Repo({})), 99)
    assert "actor_record" not in captured
    assert captured["actor"]["is_admin"] is False
