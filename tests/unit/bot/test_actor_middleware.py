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


async def _run(mw, tg_id, chat_id=None):
    captured: dict = {}

    async def handler(event, data):
        captured.update(data)
        return "ok"

    data = {"event_from_user": SimpleNamespace(id=tg_id) if tg_id else None}
    if chat_id is not None:
        data["event_chat"] = SimpleNamespace(id=chat_id)
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


# --- C5: membership in TEAM_CHAT_ID = full rights ---------------------------


@pytest.mark.asyncio
async def test_team_chat_member_is_authorized_without_record():
    """Any sender in the team chat gets full rights (no read-only tier)."""
    mw = ActorMiddleware(set(), _Repo({}), team_chat_id=-100500)
    captured = await _run(mw, 99, chat_id=-100500)
    assert captured["actor"]["is_admin"] is True
    assert captured["actor"]["in_team_chat"] is True


@pytest.mark.asyncio
async def test_known_member_authorized_even_if_not_db_admin():
    """A known team member (has a record) is authorized regardless of is_admin."""
    rec = PersonRecord(id=uuid4(), name="Тоня", is_admin=False)
    mw = ActorMiddleware(set(), _Repo({7: rec}), team_chat_id=-100500)
    captured = await _run(mw, 7)  # DM, not the team chat
    assert captured["actor_record"] is rec
    assert captured["actor"]["is_admin"] is True


@pytest.mark.asyncio
async def test_dm_from_unknown_user_outside_team_chat_not_authorized():
    mw = ActorMiddleware(set(), _Repo({}), team_chat_id=-100500)
    captured = await _run(mw, 99, chat_id=12345)  # some other private chat
    assert captured["actor"]["is_admin"] is False
    assert captured["actor"]["in_team_chat"] is False
