"""E2E test: Vacation flow (spec section 16, scenario E).

Tests SetVacationUseCase + vacation handler with FakeRepo.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest

from planner.app.ports import PersonRecord
from planner.app.set_vacation import PersonNotFoundError, SetVacationUseCase
from planner.domain.intent import VacationIntent


class FakeRepo:
    def __init__(self) -> None:
        self.people: dict[str, PersonRecord] = {}
        self.overrides: list[tuple[UUID, date, int, str | None]] = []
        self.audits: list[tuple] = []

    async def get_person_by_name(self, name: str) -> PersonRecord | None:
        return self.people.get(name)

    async def upsert_day_override(self, person_id, day, capacity_h, reason):
        self.overrides.append((person_id, day, capacity_h, reason))

    async def add_audit(self, actor_id, action, entity_type, entity_id, payload):
        self.audits.append((action, entity_type, payload))


@pytest.fixture
def repo_with_aigul():
    repo = FakeRepo()
    person_id = uuid4()
    repo.people["Айгуль"] = PersonRecord(id=person_id, name="Айгуль", is_admin=False)
    return repo, person_id


@pytest.mark.asyncio
async def test_vacation_inserts_day_overrides(repo_with_aigul):
    """Scenario E: vacation for Айгуль 10–12 June creates 3 day_overrides."""
    repo, person_id = repo_with_aigul
    actor_id = uuid4()

    intent = VacationIntent(
        person_name="Айгуль",
        day_from=date(2026, 6, 10),
        day_to=date(2026, 6, 12),
        capacity_h=0,
    )

    uc = SetVacationUseCase(repo)
    count = await uc.execute(intent, actor_id, is_admin=True)

    assert count == 3
    assert len(repo.overrides) == 3
    days = {o[1] for o in repo.overrides}
    assert days == {date(2026, 6, 10), date(2026, 6, 11), date(2026, 6, 12)}
    assert all(o[2] == 0 for o in repo.overrides)  # capacity 0 = full day off


@pytest.mark.asyncio
async def test_vacation_half_day_sets_capacity_4(repo_with_aigul):
    repo, _ = repo_with_aigul
    actor_id = uuid4()

    intent = VacationIntent(
        person_name="Айгуль",
        day_from=date(2026, 6, 15),
        day_to=date(2026, 6, 15),
        capacity_h=4,
    )
    uc = SetVacationUseCase(repo)
    count = await uc.execute(intent, actor_id, is_admin=True)
    assert count == 1
    assert repo.overrides[0][2] == 4


@pytest.mark.asyncio
async def test_vacation_person_not_found_raises():
    repo = FakeRepo()  # empty — no people
    intent = VacationIntent(
        person_name="Незнакомец",
        day_from=date(2026, 6, 1),
        day_to=date(2026, 6, 1),
    )
    uc = SetVacationUseCase(repo)
    with pytest.raises(PersonNotFoundError):
        await uc.execute(intent, uuid4(), is_admin=True)


@pytest.mark.asyncio
async def test_vacation_non_admin_raises(repo_with_aigul):
    repo, _ = repo_with_aigul
    # actor is NOT admin
    intent = VacationIntent(
        person_name="Айгуль",
        day_from=date(2026, 6, 1),
        day_to=date(2026, 6, 1),
    )
    uc = SetVacationUseCase(repo)
    with pytest.raises(PermissionError):
        await uc.execute(intent, uuid4(), is_admin=False)


@pytest.mark.asyncio
async def test_vacation_audit_recorded(repo_with_aigul):
    repo, _ = repo_with_aigul
    actor_id = uuid4()
    intent = VacationIntent(
        person_name="Айгуль",
        day_from=date(2026, 6, 20),
        day_to=date(2026, 6, 20),
    )
    uc = SetVacationUseCase(repo)
    await uc.execute(intent, actor_id, is_admin=True)
    assert len(repo.audits) == 1
    assert repo.audits[0][0] == "set_vacation"
