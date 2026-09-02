"""Unit tests for AddProjectUseCase (spec section 7.1)."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from planner.app.add_project import (
    AddProjectUseCase,
    InvalidProjectError,
    ProjectTemplate,
    TemplateTaskSpec,
    instantiate_template,
)
from planner.app.ports import PersonRecord
from planner.domain.calendar.rules import WeekendCalendar
from planner.domain.intent import AddProjectIntent
from planner.domain.models import Person
from planner.domain.solver.greedy import GreedySolver
from tests.unit.app.conftest import FakeRepo

TODAY = date(2026, 6, 5)
CAL = WeekendCalendar()


def _solver() -> GreedySolver:
    return GreedySolver(CAL)


def _person() -> Person:
    return Person(id=uuid4(), name="Андрей", capacity_h=8)


def _template(person_id) -> ProjectTemplate:
    return ProjectTemplate(
        code="standard",
        tasks=(
            TemplateTaskSpec(1, "Бриф", 8, (person_id,)),
            TemplateTaskSpec(2, "Дизайн", 8, (person_id,), depends_on_ords=(1,)),
        ),
    )


def _intent(deadline=None) -> AddProjectIntent:
    return AddProjectIntent(title="Альфа", template_code="standard", deadline=deadline)


def _actor() -> PersonRecord:
    return PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)


def test_instantiate_template_remaps_ords_to_fresh_uuids():
    p = _person()
    tasks, deps = instantiate_template(_template(p.id), project_id=uuid4())

    assert len(tasks) == 2
    assert len({t.id for t in tasks}) == 2  # unique ids
    assert len(deps) == 1
    # Template-instantiated tasks carry the 'template' provenance (spec 4).
    assert all(t.source == "template" for t in tasks)
    # Dependency edge points the design task at the brief task.
    design = next(t for t in tasks if t.name == "Дизайн")
    assert deps[0].task_id == design.id


def test_instantiate_template_two_calls_produce_disjoint_ids():
    p = _person()
    t1, _ = instantiate_template(_template(p.id), project_id=uuid4())
    t2, _ = instantiate_template(_template(p.id), project_id=uuid4())
    assert {t.id for t in t1}.isdisjoint({t.id for t in t2})


class _FakeProjectSink:
    """Records create_card calls and returns a fixed page id."""

    def __init__(self, page_id: str | None = "page-123") -> None:
        self._page_id = page_id
        self.created: list = []

    async def create_card(self, project):
        self.created.append(project)
        return self._page_id

    async def mark_task_done(self, page_id: str, task_name: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_execute_creates_notion_master_card_and_persists_page_id():
    app_fake_repo = FakeRepo()
    p = _person()
    sink = _FakeProjectSink()
    uc = AddProjectUseCase(app_fake_repo, _solver())

    result = await uc.execute(
        _intent(deadline=TODAY + timedelta(days=30)),
        _actor(),
        people=(p,),
        template=_template(p.id),
        today=TODAY,
        project_sink=sink,
    )

    assert len(sink.created) == 1
    card = sink.created[0]
    assert card.title == "Альфа"
    assert card.task_names == ("Бриф", "Дизайн")  # checklist mirrors the tasks
    assert app_fake_repo.notion_pages[result.project.id] == "page-123"


@pytest.mark.asyncio
async def test_execute_without_sink_skips_card_and_still_succeeds():
    app_fake_repo = FakeRepo()
    p = _person()
    uc = AddProjectUseCase(app_fake_repo, _solver())
    result = await uc.execute(
        _intent(), _actor(), people=(p,), template=_template(p.id), today=TODAY,
    )
    assert result.project.id in app_fake_repo.projects
    assert app_fake_repo.notion_pages == {}


@pytest.mark.asyncio
async def test_card_creation_failure_does_not_block_project():
    class _BoomSink:
        async def create_card(self, project):
            raise RuntimeError("notion down")

        async def mark_task_done(self, page_id, task_name):
            return False

    app_fake_repo = FakeRepo()
    p = _person()
    uc = AddProjectUseCase(app_fake_repo, _solver())
    result = await uc.execute(
        _intent(), _actor(), people=(p,), template=_template(p.id),
        today=TODAY, project_sink=_BoomSink(),
    )
    assert result.project.id in app_fake_repo.projects  # creation survived
    assert app_fake_repo.notion_pages == {}


@pytest.mark.asyncio
async def test_execute_persists_proposed_plan_and_audit():
    app_fake_repo = FakeRepo()
    p = _person()
    uc = AddProjectUseCase(app_fake_repo, _solver())

    result = await uc.execute(
        _intent(deadline=TODAY + timedelta(days=30)),
        _actor(),
        people=(p,),
        template=_template(p.id),
        today=TODAY,
    )

    assert result.project.id in app_fake_repo.projects
    pv = app_fake_repo.plan_versions[result.plan_version_id]
    assert pv.status == "proposed"
    assert pv.payload["assignments"]  # plan got serialized
    assert result.earliest_end is None  # forward mode
    assert any(a[1] == "add_project" for a in app_fake_repo.audits)


@pytest.mark.asyncio
async def test_execute_backward_mode_reports_critical_path():
    app_fake_repo = FakeRepo()
    p = _person()
    uc = AddProjectUseCase(app_fake_repo, _solver())

    result = await uc.execute(
        _intent(deadline=None),
        _actor(),
        people=(p,),
        template=_template(p.id),
        today=TODAY,
    )

    assert result.earliest_end is not None


@pytest.mark.asyncio
async def test_execute_backward_mode_adds_one_working_day_buffer():
    """Backward mode presents the earliest real date + a 1 working-day buffer.

    The customer's target is 6 working days of work plus 1 day of slack, so a
    two-day buffer would report 8 days against a 7-day promise
    (docs/customer-update-2026-08.md §3).
    """
    from planner.domain.calendar.rules import nth_working_day

    app_fake_repo = FakeRepo()
    p = _person()
    uc = AddProjectUseCase(app_fake_repo, _solver())

    result = await uc.execute(
        _intent(deadline=None),
        _actor(),
        people=(p,),
        template=_template(p.id),
        today=TODAY,
    )

    # Two-task FS chain (8h each, cap 8 → 1 day each) → raw critical path is 2
    # working days; the presented date adds a +1 working-day buffer (→ 3).
    assert result.earliest_end == nth_working_day(CAL, TODAY, 3)


@pytest.mark.asyncio
async def test_execute_rejects_blank_title():
    app_fake_repo = FakeRepo()
    p = _person()
    uc = AddProjectUseCase(app_fake_repo, _solver())
    intent = AddProjectIntent(title="   ", template_code="standard")

    with pytest.raises(InvalidProjectError):
        await uc.execute(intent, _actor(), people=(p,), template=_template(p.id), today=TODAY)


@pytest.mark.asyncio
async def test_execute_rejects_past_deadline():
    app_fake_repo = FakeRepo()
    p = _person()
    uc = AddProjectUseCase(app_fake_repo, _solver())

    with pytest.raises(InvalidProjectError):
        await uc.execute(
            _intent(deadline=TODAY - timedelta(days=1)),
            _actor(),
            people=(p,),
            template=_template(p.id),
            today=TODAY,
        )


@pytest.mark.asyncio
async def test_execute_rejects_empty_template():
    app_fake_repo = FakeRepo()
    p = _person()
    uc = AddProjectUseCase(app_fake_repo, _solver())
    empty = ProjectTemplate(code="standard", tasks=())

    with pytest.raises(InvalidProjectError):
        await uc.execute(_intent(), _actor(), people=(p,), template=empty, today=TODAY)


@pytest.mark.asyncio
async def test_cyclic_template_raises_invalid_and_writes_nothing():
    """A template whose deps form a cycle must fail BEFORE any DB write."""
    person = Person(id=uuid4(), name="Андрей", capacity_h=8)
    template = ProjectTemplate(
        code="standard",
        tasks=(
            TemplateTaskSpec(1, "A", 8, (person.id,), depends_on_ords=(2,)),
            TemplateTaskSpec(2, "B", 8, (person.id,), depends_on_ords=(1,)),
        ),
    )
    repo = FakeRepo()
    intent = AddProjectIntent(title="Цикл", template_code="standard")
    with pytest.raises(InvalidProjectError):
        await AddProjectUseCase(repo, GreedySolver(WeekendCalendar())).execute(
            intent,
            PersonRecord(id=uuid4(), name="Менеджер", is_admin=True),
            (person,),
            template,
            today=date.today(),
        )
    assert repo.projects == {}       # no orphan project row
    assert repo.plan_versions == {}  # no plan version
    assert repo.audits == []         # no audit entry
    assert repo.saved_tasks == []    # no task rows written


@pytest.mark.asyncio
async def test_empty_people_pool_raises_friendly_message_and_writes_nothing():
    """Empty roster (dev DB reset) must yield a friendly error, not a traceback."""
    repo = FakeRepo()
    p = _person()  # template references a person absent from the roster
    uc = AddProjectUseCase(repo, _solver())

    with pytest.raises(InvalidProjectError, match="нет ни одного исполнителя"):
        await uc.execute(
            _intent(), _actor(), people=(), template=_template(p.id), today=TODAY,
        )

    assert repo.projects == {}       # no orphan project row
    assert repo.plan_versions == {}  # no plan version
    assert repo.saved_tasks == []    # no task rows written


@pytest.mark.asyncio
async def test_add_project_persists_tasks_with_schedule():
    repo = FakeRepo()
    p = _person()
    uc = AddProjectUseCase(repo, _solver())

    result = await uc.execute(
        _intent(deadline=TODAY + timedelta(days=30)),
        _actor(),
        people=(p,),
        template=_template(p.id),
        today=TODAY,
    )

    assert repo.saved_tasks, "tasks must be persisted to the tasks table"
    saved_project_id, saved_tasks, saved_assignments = repo.saved_tasks[0]
    assert saved_project_id == result.project.id
    assert {t.id for t in saved_tasks} == {t.id for t in result.tasks}
    assert len(saved_assignments) == len(result.plan.assignments)
