"""Tests for the AddProject reply orchestrator in the task router (spec 8.1)."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from planner.app.add_project import ProjectTemplate, TemplateTaskSpec
from planner.app.ports import PersonRecord
from planner.bot.handlers.task_router import build_add_project_reply
from planner.domain.calendar.rules import WeekendCalendar
from planner.domain.intent import AddProjectIntent
from planner.domain.models import Person
from planner.domain.solver.greedy import GreedySolver
from tests.unit.app.conftest import FakeRepo

TODAY = date(2026, 6, 5)


def _setup_repo() -> tuple[FakeRepo, Person]:
    repo = FakeRepo()
    andrey = Person(id=uuid4(), name="Андрей", capacity_h=8)
    repo.solver_people = (andrey,)
    repo.templates = {
        "standard": ProjectTemplate(
            code="standard",
            tasks=(
                TemplateTaskSpec(1, "Бриф", 8, (andrey.id,)),
                TemplateTaskSpec(2, "Дизайн", 8, (andrey.id,), depends_on_ords=(1,)),
            ),
        )
    }
    return repo, andrey


def _admin() -> PersonRecord:
    return PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)


@pytest.mark.asyncio
async def test_reply_contains_project_and_tasks():
    repo, _ = _setup_repo()
    intent = AddProjectIntent(
        title="Альфа", template_code="standard", deadline=TODAY + timedelta(days=30)
    )
    reply = await build_add_project_reply(
        intent,
        repo=repo,
        solver=GreedySolver(WeekendCalendar()),
        actor_record=_admin(),
        today=TODAY,
    )
    assert "Альфа" in reply
    assert "Бриф → Андрей" in reply
    assert "Дизайн → Андрей" in reply
    # A proposed plan version was persisted.
    assert any(pv.status == "proposed" for pv in repo.plan_versions.values())


@pytest.mark.asyncio
async def test_unknown_template_message():
    repo, _ = _setup_repo()
    intent = AddProjectIntent(title="Альфа", template_code="lite")
    reply = await build_add_project_reply(
        intent,
        repo=repo,
        solver=GreedySolver(WeekendCalendar()),
        actor_record=_admin(),
        today=TODAY,
    )
    assert "не найден" in reply


@pytest.mark.asyncio
async def test_no_people_message():
    repo, _ = _setup_repo()
    repo.solver_people = ()
    intent = AddProjectIntent(title="Альфа", template_code="standard")
    reply = await build_add_project_reply(
        intent,
        repo=repo,
        solver=GreedySolver(WeekendCalendar()),
        actor_record=_admin(),
        today=TODAY,
    )
    assert "нет активных людей" in reply


@pytest.mark.asyncio
async def test_past_deadline_message():
    repo, _ = _setup_repo()
    intent = AddProjectIntent(
        title="Альфа", template_code="standard", deadline=TODAY - timedelta(days=1)
    )
    reply = await build_add_project_reply(
        intent,
        repo=repo,
        solver=GreedySolver(WeekendCalendar()),
        actor_record=_admin(),
        today=TODAY,
    )
    assert "Не могу создать проект" in reply
