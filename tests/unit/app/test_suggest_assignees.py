"""Unit tests for SuggestAssigneesUseCase (spec section 5)."""

from uuid import uuid4

import pytest

from planner.app.ports import CapabilityRecord
from planner.app.suggest_assignees import SuggestAssigneesUseCase
from tests.unit.app.conftest import FakeRepo


def _cap(name, skills, *, external=False):
    return CapabilityRecord(
        person_id=uuid4(),
        name=name,
        skills=frozenset(skills),
        is_external=external,
    )


def _committed_payload(person_id, hours):
    return {
        "assignments": [
            {
                "task_id": str(uuid4()),
                "person_id": str(person_id),
                "start_date": "2026-06-01",
                "end_date": "2026-06-01",
                "allocations": [
                    {"person_id": str(person_id), "day": "2026-06-01", "hours": hours}
                ],
            }
        ],
        "risks": [],
        "end_date": "2026-06-01",
    }


@pytest.mark.asyncio
async def test_ranks_by_coverage():
    repo = FakeRepo()
    full = _cap("Full", {"Копирайтинг", "Редактура"})
    partial = _cap("Partial", {"Копирайтинг"})
    repo.capabilities = [partial, full]
    uc = SuggestAssigneesUseCase(repo)
    out = await uc.execute(["Копирайтинг", "Редактура"])
    assert [s.name for s in out] == ["Full", "Partial"]


@pytest.mark.asyncio
async def test_committed_load_breaks_ties():
    repo = FakeRepo()
    busy = _cap("Busy", {"S"})
    free = _cap("Free", {"S"})
    repo.capabilities = [busy, free]
    repo.committed_payloads = [_committed_payload(busy.person_id, 16)]
    uc = SuggestAssigneesUseCase(repo)
    out = await uc.execute(["S"])
    assert [s.name for s in out] == ["Free", "Busy"]
    assert out[1].load_hours == 16
    assert out[0].load_hours == 0


@pytest.mark.asyncio
async def test_external_excluded_by_default():
    repo = FakeRepo()
    ext = _cap("Катя", {"Дизайн макетов"}, external=True)
    repo.capabilities = [ext]
    uc = SuggestAssigneesUseCase(repo)
    assert await uc.execute(["Дизайн макетов"]) == ()
    out = await uc.execute(["Дизайн макетов"], include_external=True)
    assert [s.name for s in out] == ["Катя"]


@pytest.mark.asyncio
async def test_limit_applied():
    repo = FakeRepo()
    repo.capabilities = [_cap(f"P{i}", {"S"}) for i in range(8)]
    uc = SuggestAssigneesUseCase(repo)
    out = await uc.execute(["S"], limit=3)
    assert len(out) == 3
