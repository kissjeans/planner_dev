"""Unit tests for ConfirmPlanUseCase (spec section 7.3)."""

from uuid import uuid4

import pytest

from planner.app.confirm_plan import (
    ConfirmPlanUseCase,
    PlanNotFoundError,
    PlanNotProposedError,
)
from planner.app.ports import PersonRecord, PlanVersionRecord
from tests.unit.app.conftest import FakeRepo

ADMIN = PersonRecord(id=uuid4(), name="Admin", is_admin=True)
MEMBER = PersonRecord(id=uuid4(), name="Member", is_admin=False)


def _proposed(repo: FakeRepo) -> PlanVersionRecord:
    pv = PlanVersionRecord(uuid4(), uuid4(), "proposed", {"tasks": []})
    repo.plan_versions[pv.id] = pv
    return pv


async def test_admin_commits_proposed_plan():
    repo = FakeRepo()
    pv = _proposed(repo)
    result = await ConfirmPlanUseCase(repo).execute(pv.id, ADMIN)
    assert result.status == "committed"
    assert repo.plan_versions[pv.id].status == "committed"
    assert repo.audits and repo.audits[0][1] == "confirm_plan"


async def test_member_cannot_commit():
    repo = FakeRepo()
    pv = _proposed(repo)
    with pytest.raises(PermissionError):
        await ConfirmPlanUseCase(repo).execute(pv.id, MEMBER)


async def test_missing_plan_raises():
    repo = FakeRepo()
    with pytest.raises(PlanNotFoundError):
        await ConfirmPlanUseCase(repo).execute(uuid4(), ADMIN)


async def test_already_committed_raises():
    repo = FakeRepo()
    pv = PlanVersionRecord(uuid4(), uuid4(), "committed", {})
    repo.plan_versions[pv.id] = pv
    with pytest.raises(PlanNotProposedError):
        await ConfirmPlanUseCase(repo).execute(pv.id, ADMIN)


async def test_double_confirm_second_raises():
    """Two confirms of the same plan: first wins, second gets PlanNotProposedError
    and writes no second audit entry (the TOCTOU regression)."""
    repo = FakeRepo()
    pv = _proposed(repo)
    uc = ConfirmPlanUseCase(repo)
    await uc.execute(pv.id, ADMIN)
    with pytest.raises(PlanNotProposedError):
        await uc.execute(pv.id, ADMIN)
    assert len(repo.audits) == 1
