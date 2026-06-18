"""Button-clarify flow for missing key task fields (исполнитель/проект/дедлайн)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from planner.app.ports import PersonRecord, ProjectRecord
from planner.bot.handlers import clarify

# Reuse the proven repo double from the ToolBox suite (DRY).
from tests.unit.infra.test_toolbox import FakeRepo, _admin_record

_ADMIN = {"is_admin": True}


class _FakeMsg:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.markups: list[object] = []

    async def answer(self, text: str, reply_markup: object = None) -> None:
        self.sent.append(text)
        self.markups.append(reply_markup)


class _FakeState:
    def __init__(self) -> None:
        self._d: dict[str, object] = {}
        self.state: object = None

    async def get_data(self) -> dict[str, object]:
        return dict(self._d)

    async def update_data(self, **kw: object) -> dict[str, object]:
        self._d.update(kw)
        return dict(self._d)

    async def set_state(self, s: object) -> None:
        self.state = s

    async def clear(self) -> None:
        self._d = {}
        self.state = None


# --- pure logic -----------------------------------------------------------

def test_missing_lists_fields_in_ask_order():
    assert clarify._missing({}) == ["project", "assignee", "deadline"]
    assert clarify._missing(
        {"project": "МТС", "assignees": ["Андрей"], "deadline": "2026-06-21"}
    ) == []
    assert clarify._missing({"project": "МТС"}) == ["assignee", "deadline"]


@pytest.mark.parametrize("text,expected", [
    ("2026-06-21", date(2026, 6, 21)),
    ("21.06.2026", date(2026, 6, 21)),
    ("21.06", date(date.today().year, 6, 21)),
    ("не дата", None),
    ("", None),
])
def test_parse_date(text, expected):
    assert clarify._parse_date(text) == expected


# --- finalize -------------------------------------------------------------

_FULL = {
    "title": "КП", "assignees": ["Андрей"], "project": "МТС",
    "deadline": "2026-06-21", "est_hours": None, "required_skills": [],
}


@pytest.mark.asyncio
async def test_finalize_admin_captures_and_confirms():
    repo, msg, state = FakeRepo(), _FakeMsg(), _FakeState()
    await clarify._finalize(msg, state, dict(_FULL), repo, None, _admin_record(), _ADMIN)
    assert repo.created_tasks and repo.created_tasks[0]["name"] == "КП"
    assert "Записал" in msg.sent[-1]
    assert state._d == {}  # FSM cleared


@pytest.mark.asyncio
async def test_finalize_non_admin_blocked():
    repo, msg, state = FakeRepo(), _FakeMsg(), _FakeState()
    await clarify._finalize(msg, state, dict(_FULL), repo, None, None, {"is_admin": False})
    assert repo.created_tasks == []
    assert "админ" in msg.sent[-1].lower()


# --- advancing through fields --------------------------------------------

@pytest.mark.asyncio
async def test_after_fill_advances_to_next_missing_with_buttons():
    repo = FakeRepo(
        projects=[ProjectRecord(uuid4(), "МТС", "planning")],
        people=[PersonRecord(uuid4(), "Андрей")],
    )
    msg, state = _FakeMsg(), _FakeState()
    # Project just chosen; исполнитель + дедлайн still missing → ask assignee next.
    args = {"title": "КП", "project": "МТС", "assignees": [], "deadline": None}
    await clarify._after_fill(msg, state, args, repo, None, _admin_record(), _ADMIN)
    assert "исполнитель" in msg.sent[-1].lower()
    assert msg.markups[-1] is not None  # buttons, not free text
    assert repo.created_tasks == []  # nothing written until all three known


@pytest.mark.asyncio
async def test_start_asks_first_missing_field():
    repo = FakeRepo(projects=[ProjectRecord(uuid4(), "МТС", "planning")])
    msg, state = _FakeMsg(), _FakeState()
    pending = {"title": "КП", "project": "", "assignees": [], "deadline": None}
    await clarify.start_capture_clarify(msg, state, pending, repo)
    assert "проект" in msg.sent[-1].lower()
    assert msg.markups[-1] is not None
    assert (await state.get_data())["capture"]["title"] == "КП"
