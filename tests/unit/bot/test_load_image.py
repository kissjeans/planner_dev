"""Tests for the /load heatmap orchestrator (spec 7.4 / 8.1)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from planner.app.add_project import serialize_plan
from planner.bot.handlers.load import build_load_image
from planner.domain.models import Assignment, DayAllocation, Person, PlanResult
from tests.unit.app.conftest import FakeRepo

START = date(2026, 6, 8)


def _committed_payload(person_id):
    plan = PlanResult(
        assignments=(
            Assignment(
                task_id=uuid4(),
                person_id=person_id,
                start_date=START,
                end_date=START,
                allocations=(DayAllocation(person_id, START, 8),),
            ),
        )
    )
    return serialize_plan(plan)


@pytest.mark.asyncio
async def test_returns_png_bytes():
    repo = FakeRepo()
    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo.solver_people = (andrey,)
    repo.committed_payloads = [_committed_payload(andrey.id)]

    png = await build_load_image(repo, start=START)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic header


@pytest.mark.asyncio
async def test_none_without_people():
    repo = FakeRepo()
    repo.solver_people = ()
    assert await build_load_image(repo, start=START) is None


@pytest.mark.asyncio
async def test_person_filter_renders_only_that_person():
    repo = FakeRepo()
    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    aigul = Person(id=uuid4(), name="Айгуль", capacity_h=8)
    repo.solver_people = (andrey, aigul)
    repo.committed_payloads = [_committed_payload(andrey.id)]

    full = await build_load_image(repo, start=START)
    filtered = await build_load_image(repo, start=START, person_name="Айгуль")
    assert full is not None and filtered is not None
    # Filtering to one person yields a different (smaller) heatmap than the team.
    assert filtered != full


@pytest.mark.asyncio
async def test_unknown_person_falls_back_to_whole_team():
    repo = FakeRepo()
    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo.solver_people = (andrey,)
    repo.committed_payloads = [_committed_payload(andrey.id)]

    full = await build_load_image(repo, start=START)
    unknown = await build_load_image(repo, start=START, person_name="Призрак")
    assert unknown == full
