"""Unit tests for SetVacationUseCase (spec section 7.4 / 5)."""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from planner.app.ports import PersonRecord
from planner.app.set_vacation import (
    MAX_VACATION_DAYS,
    InvalidVacationError,
    PersonNotFoundError,
    SetVacationUseCase,
)
from planner.domain.intent import VacationIntent
from tests.unit.app.conftest import FakeRepo

ADMIN_ID = uuid4()


def _intent(**kwargs):
    defaults = dict(
        person_name="Айгуль",
        day_from=date(2026, 6, 10),
        day_to=date(2026, 6, 12),
    )
    defaults.update(kwargs)
    return VacationIntent(**defaults)


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


async def test_reversed_range_raises_and_writes_nothing():
    repo = FakeRepo()
    repo.people["Айгуль"] = PersonRecord(id=uuid4(), name="Айгуль")
    intent = _intent(day_from=date(2026, 6, 12), day_to=date(2026, 6, 10))
    with pytest.raises(InvalidVacationError):
        await SetVacationUseCase(repo).execute(intent, ADMIN_ID, is_admin=True)
    assert repo.overrides == []
    assert repo.audits == []


async def test_range_longer_than_max_rejected():
    repo = FakeRepo()
    repo.people["Айгуль"] = PersonRecord(id=uuid4(), name="Айгуль")
    intent = _intent(
        day_from=date(2026, 6, 1),
        day_to=date(2026, 6, 1) + timedelta(days=MAX_VACATION_DAYS),  # 61 days
    )
    with pytest.raises(InvalidVacationError):
        await SetVacationUseCase(repo).execute(intent, ADMIN_ID, is_admin=True)
    assert repo.overrides == []
    assert repo.audits == []


async def test_range_at_max_is_allowed():
    repo = FakeRepo()
    repo.people["Айгуль"] = PersonRecord(id=uuid4(), name="Айгуль")
    intent = _intent(
        day_from=date(2026, 6, 1),
        day_to=date(2026, 6, 1) + timedelta(days=MAX_VACATION_DAYS - 1),  # 60 days
    )
    count = await SetVacationUseCase(repo).execute(intent, ADMIN_ID, is_admin=True)
    assert count == MAX_VACATION_DAYS


async def test_capacity_above_max_rejected():
    repo = FakeRepo()
    repo.people["Айгуль"] = PersonRecord(id=uuid4(), name="Айгуль")
    with pytest.raises(InvalidVacationError):
        await SetVacationUseCase(repo).execute(
            _intent(capacity_h=13), ADMIN_ID, is_admin=True
        )
    assert repo.overrides == []
    assert repo.audits == []


async def test_negative_capacity_rejected():
    repo = FakeRepo()
    repo.people["Айгуль"] = PersonRecord(id=uuid4(), name="Айгуль")
    with pytest.raises(InvalidVacationError):
        await SetVacationUseCase(repo).execute(
            _intent(capacity_h=-1), ADMIN_ID, is_admin=True
        )
    assert repo.overrides == []
    assert repo.audits == []
