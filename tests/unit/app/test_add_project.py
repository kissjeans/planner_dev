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
    # Dependency edge points the design task at the brief task.
    design = next(t for t in tasks if t.name == "Дизайн")
    assert deps[0].task_id == design.id


def test_instantiate_template_two_calls_produce_disjoint_ids():
    p = _person()
    t1, _ = instantiate_template(_template(p.id), project_id=uuid4())
    t2, _ = instantiate_template(_template(p.id), project_id=uuid4())
    assert {t.id for t in t1}.isdisjoint({t.id for t in t2})


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
