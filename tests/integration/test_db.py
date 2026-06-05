"""Integration tests for SqlAlchemyRepo against a real Postgres container (spec 12.1)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import insert, text

from planner.infra.db.models import Person
from planner.infra.db.repo import SqlAlchemyRepo


@pytest.fixture(scope="module")
def repo(db_session_factory):
    return SqlAlchemyRepo(db_session_factory)


@pytest_asyncio.fixture
async def seed_person(db_session_factory):
    """Insert a single person for use in tests; cleaned up via session rollback."""
    person_id = uuid4()
    async with db_session_factory() as s, s.begin():
        await s.execute(
            insert(Person).values(
                id=person_id,
                name="Тест Иванов",
                is_admin=True,
                is_active=True,
                capacity_h=8,
            )
        )
    yield person_id
    # cleanup
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})


@pytest.mark.asyncio
async def test_get_person_by_name_returns_record(repo, seed_person):
    rec = await repo.get_person_by_name("Тест Иванов")
    assert rec is not None
    assert rec.name == "Тест Иванов"
    assert rec.is_admin is True


@pytest.mark.asyncio
async def test_get_person_by_name_missing_returns_none(repo):
    rec = await repo.get_person_by_name("Никого нет")
    assert rec is None


@pytest.mark.asyncio
async def test_upsert_day_override_inserts_and_updates(repo, seed_person):
    day = date(2026, 7, 1)
    await repo.upsert_day_override(seed_person, day, 0, "vacation")

    # upsert again with updated capacity
    await repo.upsert_day_override(seed_person, day, 4, "half day")

    from sqlalchemy import select

    from planner.infra.db.models import DayOverride
    async with repo._sf() as s:
        row = await s.scalar(
            select(DayOverride).where(
                DayOverride.person_id == seed_person,
                DayOverride.day == day,
            )
        )
    assert row is not None
    assert row.capacity_h == 4
    assert row.reason == "half day"
    # cleanup
    async with repo._sf() as s, s.begin():
        await s.execute(
            text("DELETE FROM day_overrides WHERE person_id = :pid AND day = :d"),
            {"pid": seed_person, "d": day},
        )


@pytest.mark.asyncio
async def test_list_people_returns_active_only(repo, seed_person):
    people = await repo.list_people()
    names = [p.name for p in people]
    assert "Тест Иванов" in names


@pytest.mark.asyncio
async def test_save_and_get_plan_version(repo, db_session_factory):
    project_id = uuid4()
    # need a project row to satisfy FK
    from planner.infra.db.models import Project
    async with db_session_factory() as s, s.begin():
        s.add(Project(id=project_id, title="Тест", status="planning"))

    payload = {"assignments": [], "risks": [], "end_date": None}
    pv = await repo.save_plan_version(project_id, "proposed", payload, None)
    assert pv.status == "proposed"
    assert pv.project_id == project_id

    fetched = await repo.get_plan_version(pv.id)
    assert fetched is not None
    assert fetched.payload == payload

    # cleanup
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM plan_versions WHERE id = :id"), {"id": pv.id})
        await s.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})


@pytest.mark.asyncio
async def test_add_audit_does_not_raise(repo):
    await repo.add_audit(None, "test_action", "person", None, {"note": "test"})
