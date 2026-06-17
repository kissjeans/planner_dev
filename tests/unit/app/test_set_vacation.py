"""Unit tests for SetVacationUseCase (spec section 7.4 / 5)."""

from datetime import date
from uuid import uuid4

import pytest

from planner.app.ports import PersonRecord
from planner.app.set_vacation import PersonNotFoundError, SetVacationUseCase
from planner.domain.intent import VacationIntent
from tests.unit.app.conftest import FakeRepo

ADMIN_ID = uuid4()


def _intent():
    return VacationIntent(
        person_name="Айгуль",
        day_from=date(2026, 6, 10),
        day_to=date(2026, 6, 12),
    )


async def test_writes_override_per_day_inclusive():
    repo = FakeRepo()
    repo.people["Айгуль"] = PersonRecord(id=uuid4(), name="Айгуль")
    count = await SetVacationUseCase(repo).execute(_intent(), ADMIN_ID, is_admin=True)
    assert count == 3
    assert len(repo.overrides) == 3
    assert {o[1] for o in repo.overrides} == {
        date(2026, 6, 10), date(2026, 6, 11), date(2026, 6, 12)
    }
    assert all(o[2] == 0 for o in repo.overrides)  # full day off
    assert repo.audits and repo.audits[0][1] == "set_vacation"


async def test_member_cannot_set_vacation():
    repo = FakeRepo()
    with pytest.raises(PermissionError):
        await SetVacationUseCase(repo).execute(_intent(), uuid4(), is_admin=False)


async def test_unknown_person_raises():
    repo = FakeRepo()
    with pytest.raises(PersonNotFoundError):
        await SetVacationUseCase(repo).execute(_intent(), ADMIN_ID, is_admin=True)
