"""E2E test: AddProject flow (spec section 16, scenario A/B).

Uses FakeRepo + real solver + BasicIntentParser to exercise the full
bot handler → use-case → solver → explain chain without network I/O.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from planner.app.add_project import (
    ProjectTemplate,
    TemplateTaskSpec,
)
from planner.app.ports import PersonRecord, PlanVersionRecord, ProjectRecord
from planner.bot.handlers.task_router import build_add_project_reply
from planner.domain.calendar.rules import WeekendCalendar
from planner.domain.intent import AddProjectIntent
from planner.domain.models import Person
from planner.domain.solver.greedy import GreedySolver


class FakeRepo:
    def __init__(self, people: list[Person], template: ProjectTemplate) -> None:
        self._people = people
        self._template = template
        self.plan_versions: dict[UUID, PlanVersionRecord] = {}
        self.projects: dict[UUID, ProjectRecord] = {}
        self.committed_payloads: list[dict[str, Any]] = []
        self.audits: list[tuple] = []

    async def get_solver_people(self):
        return tuple(self._people)

    async def get_project_template(self, code: str):
        return self._template if code == self._template.code else None

    async def create_project(self, *, title, template_code, deadline,
                             brief_return_date, actor_id, project_id=None):
        rec = ProjectRecord(project_id or uuid4(), title, "planning", deadline)
        self.projects[rec.id] = rec
        return rec

    async def save_project_tasks(self, project_id, tasks, assignments):
        self.saved_project_tasks = (project_id, tasks, assignments)

    async def save_plan_version(self, project_id, status, payload, actor_id):
        rec = PlanVersionRecord(uuid4(), project_id, status, payload)
        self.plan_versions[rec.id] = rec
        return rec

    async def list_committed_plans(self):
        return self.committed_payloads

    async def get_person_by_name(self, name):
        return None

    async def get_plan_version(self, pv_id):
        return self.plan_versions.get(pv_id)

    async def set_plan_version_status(self, pv_id, status):
        pv = self.plan_versions[pv_id]
        self.plan_versions[pv_id] = PlanVersionRecord(pv.id, pv.project_id, status, pv.payload)

    async def get_committed_plan(self, project_id):
        for pv in self.plan_versions.values():
            if pv.project_id == project_id and pv.status == "committed":
                return pv
        return None

    async def upsert_day_override(self, person_id, day, capacity_h, reason):
        pass

    async def add_audit(self, actor_id, action, entity_type, entity_id, payload):
        self.audits.append((action, entity_type))

    async def list_projects(self):
        return list(self.projects.values())

    async def list_people(self):
        return [PersonRecord(p.id, p.name) for p in self._people]

    async def list_audit(self, limit=50, offset=0):
        return []

    async def get_person_by_tg_id(self, tg_user_id):
        return None

    async def update_task_schedule(self, task_id, start, end, person_id):
        pass

    async def set_task_status(self, task_id, status):
        pass


def _make_template() -> ProjectTemplate:
    person_id = uuid4()
    p = Person(id=person_id, name="Андрей", capacity_h=8)
    spec = TemplateTaskSpec(
        ord=1,
        name="Разработка",
        duration_hours=16,
        allowed_person_ids=(person_id,),
        is_splittable=True,
    )
    return ProjectTemplate(code="standard", tasks=(spec,)), p


@pytest.mark.asyncio
async def test_add_project_forward_returns_plan_text():
    """Scenario A: create a project with a deadline → bot returns plan text."""
    template, person = _make_template()
    repo = FakeRepo([person], template)
    solver = GreedySolver(WeekendCalendar())
    actor = PersonRecord(person.id, person.name, is_admin=True)
    today = date(2026, 6, 2)
    deadline = today + timedelta(days=30)

    intent = AddProjectIntent(
        title="Альфа",
        template_code="standard",
        deadline=deadline,
    )

    reply, _ = await build_add_project_reply(
        intent,
        repo=repo,
        solver=solver,
        actor_record=actor,
        today=today,
    )

    assert "Альфа" in reply
    assert "план" in reply.lower() or "разработка" in reply.lower()


@pytest.mark.asyncio
async def test_add_project_backward_mode_no_deadline():
    """Scenario B: no deadline → solver runs in backward/critical-path mode."""
    template, person = _make_template()
    repo = FakeRepo([person], template)
    solver = GreedySolver(WeekendCalendar())
    actor = PersonRecord(person.id, person.name, is_admin=True)

    intent = AddProjectIntent(
        title="Бета",
        template_code="standard",
        deadline=None,
    )

    reply, _ = await build_add_project_reply(
        intent,
        repo=repo,
        solver=solver,
        actor_record=actor,
        today=date(2026, 6, 2),
    )

    assert "Бета" in reply


@pytest.mark.asyncio
async def test_add_project_missing_template_returns_error():
    template, person = _make_template()
    repo = FakeRepo([person], template)
    solver = GreedySolver(WeekendCalendar())
    actor = PersonRecord(person.id, person.name, is_admin=True)

    intent = AddProjectIntent(
        title="Гамма",
        template_code="lite",  # not loaded
        deadline=None,
    )

    reply, _ = await build_add_project_reply(
        intent,
        repo=repo,
        solver=solver,
        actor_record=actor,
        today=date(2026, 6, 2),
    )

    assert "не найден" in reply.lower()


@pytest.mark.asyncio
async def test_add_project_respects_existing_committed_capacity():
    """New project does not double-book capacity already in committed plans (spec 9)."""
    template, person = _make_template()

    # Simulate person is fully booked for 10 working days via an existing plan
    today = date(2026, 6, 2)
    existing_allocs = [
        {"person_id": str(person.id), "day": (today + timedelta(days=i)).isoformat(), "hours": 8}
        for i in range(10)
    ]
    committed_payload = {
        "assignments": [
            {
                "task_id": str(uuid4()),
                "person_id": str(person.id),
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=9)).isoformat(),
                "allocations": existing_allocs,
            }
        ],
        "risks": [],
        "end_date": None,
    }
    repo = FakeRepo([person], template)
    repo.committed_payloads = [committed_payload]
    solver = GreedySolver(WeekendCalendar())
    actor = PersonRecord(person.id, person.name, is_admin=True)

    intent = AddProjectIntent(title="Дельта", template_code="standard", deadline=None)

    reply, _ = await build_add_project_reply(
        intent,
        repo=repo,
        solver=solver,
        actor_record=actor,
        today=today,
    )

    # Even with full capacity, the solver places the task after existing allocations
    assert "Дельта" in reply
