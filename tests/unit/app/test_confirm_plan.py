"""Unit tests for ConfirmPlanUseCase (spec section 7.3)."""

from datetime import date
from uuid import UUID, uuid4

import pytest

from planner.app.confirm_plan import (
    ConfirmPlanUseCase,
    PlanNotFoundError,
    PlanNotProposedError,
)
from planner.app.ports import PersonRecord, PlanVersionRecord, ProjectRecord, SinkTask
from tests.unit.app.conftest import FakeRepo

ADMIN = PersonRecord(id=uuid4(), name="Admin", is_admin=True)
MEMBER = PersonRecord(id=uuid4(), name="Member", is_admin=False)


def _proposed(repo: FakeRepo, payload: dict | None = None) -> PlanVersionRecord:
    pv = PlanVersionRecord(uuid4(), uuid4(), "proposed", payload or {"tasks": []})
    repo.plan_versions[pv.id] = pv
    return pv


async def test_admin_commits_proposed_plan():
    repo = FakeRepo()
    pv = _proposed(repo)
    result = await ConfirmPlanUseCase(repo).execute(pv.id, ADMIN)
    assert result.plan.status == "committed"
    assert repo.plan_versions[pv.id].status == "committed"
    assert repo.audits and repo.audits[0][1] == "confirm_plan"


async def test_registered_member_commits_proposed_plan():
    """Any chat participant can confirm — admin rights not required (9ca5b61)."""
    repo = FakeRepo()
    pv = _proposed(repo)
    result = await ConfirmPlanUseCase(repo).execute(pv.id, MEMBER)
    assert result.plan.status == "committed"
    assert repo.plan_versions[pv.id].status == "committed"
    assert repo.audits and repo.audits[0][0] == MEMBER.id


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


# ---------------------------------------------------------------------------
# Notion mirror (_mirror_tasks)
# ---------------------------------------------------------------------------

class MirrorRepo(FakeRepo):
    """FakeRepo extended with the reads _mirror_tasks needs."""

    def __init__(self) -> None:
        super().__init__()
        self.task_names: dict[UUID, str] = {}
        self.mirror_people: list[PersonRecord] = []

    async def get_project(self, project_id: UUID) -> ProjectRecord | None:
        return self.projects.get(project_id)

    async def get_task_name_map(self) -> dict[UUID, str]:
        return dict(self.task_names)

    async def list_people(self) -> list[PersonRecord]:
        return list(self.mirror_people)


class FakeSink:
    """TaskSinkPort double: fails (returns None) for titles in ``fail_titles``."""

    def __init__(self, fail_titles: set[str] | None = None) -> None:
        self.pushed: list[SinkTask] = []
        self._fail = fail_titles or set()

    async def push_task(self, task: SinkTask) -> str | None:
        self.pushed.append(task)
        return None if task.title in self._fail else "https://notion.so/x"


def _mirror_fixture() -> tuple[MirrorRepo, PlanVersionRecord]:
    repo = MirrorRepo()
    person = PersonRecord(id=uuid4(), name="Айгуль", is_admin=False)
    repo.mirror_people = [person]
    t1, t2 = uuid4(), uuid4()
    repo.task_names = {t1: "Бриф", t2: "КП"}
    payload = {
        "assignments": [
            {
                "task_id": str(t1),
                "person_id": str(person.id),
                "start_date": "2026-07-06",
                "end_date": "2026-07-08",
                "allocations": [],
            },
            {
                "task_id": str(t2),
                "person_id": str(person.id),
                "start_date": "2026-07-09",
                "end_date": "2026-07-10",
                "allocations": [],
            },
        ]
    }
    pv = _proposed(repo, payload)
    repo.projects[pv.project_id] = ProjectRecord(
        pv.project_id, "Пилот", "planning", None
    )
    return repo, pv


async def test_mirror_all_success_pushes_end_date_as_deadline():
    repo, pv = _mirror_fixture()
    sink = FakeSink()
    result = await ConfirmPlanUseCase(repo, sink).execute(pv.id, ADMIN)
    assert result.mirror_total == 2
    assert result.mirror_failed == 0
    assert [t.title for t in sink.pushed] == ["Бриф", "КП"]
    # The Notion due date is the assignment's end date, not its start date.
    assert [t.deadline for t in sink.pushed] == [date(2026, 7, 8), date(2026, 7, 10)]
    assert all(t.assignees == ["Айгуль"] for t in sink.pushed)


async def test_mirror_partial_failure_counted():
    repo, pv = _mirror_fixture()
    sink = FakeSink(fail_titles={"КП"})
    result = await ConfirmPlanUseCase(repo, sink).execute(pv.id, ADMIN)
    assert result.plan.status == "committed"  # commit itself never blocks on Notion
    assert result.mirror_total == 2
    assert result.mirror_failed == 1


async def test_mirror_null_sink_noop():
    from planner.infra.notion.client import NullTaskSink

    repo, pv = _mirror_fixture()
    result = await ConfirmPlanUseCase(repo, NullTaskSink()).execute(pv.id, ADMIN)
    assert result.plan.status == "committed"
    assert result.mirror_total == 0
    assert result.mirror_failed == 0
