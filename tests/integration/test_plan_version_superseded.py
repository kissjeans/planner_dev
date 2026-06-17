"""Integration test: plan_versions.status allows 'superseded' (cluster 1 fix).

The edit-loop (bot/handlers/task_router.py) retires the replaced proposal via
``repo.transition_plan_status(old_pv_id, 'proposed', 'superseded')``. Migration
0004's ``ck_plan_version_status`` only allowed ('proposed','committed'), so on
real Postgres every plan edit raised IntegrityError. Migration 0006 widens the
constraint to include 'superseded'; this test proves constraint + code agree.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from planner.infra.db.models import PlanVersion, Project
from planner.infra.db.repo import SqlAlchemyRepo


@pytest.fixture(scope="module")
def repo(db_session_factory):
    return SqlAlchemyRepo(db_session_factory)


@pytest_asyncio.fixture
async def proposed_plan(db_session_factory):
    project_id = uuid4()
    pv_id = uuid4()
    payload = {"assignments": [], "risks": [], "end_date": None}
    async with db_session_factory() as s, s.begin():
        s.add(Project(id=project_id, title="Superseded Проект", status="planning"))
        s.add(PlanVersion(id=pv_id, project_id=project_id,
                          status="proposed", payload=payload))
    yield pv_id
    async with db_session_factory() as s, s.begin():
        await s.execute(
            text("DELETE FROM plan_versions WHERE project_id = :id"),
            {"id": project_id},
        )
        await s.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})


@pytest.mark.asyncio
async def test_transition_proposed_to_superseded_succeeds(repo, proposed_plan):
    pv_id = proposed_plan

    moved = await repo.transition_plan_status(pv_id, "proposed", "superseded")

    assert moved is True
    pv = await repo.get_plan_version(pv_id)
    assert pv is not None
    assert pv.status == "superseded"
