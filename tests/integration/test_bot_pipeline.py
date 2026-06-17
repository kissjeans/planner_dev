"""Bot-pipeline integration test (spec section 16 — closes the live-E2E gap).

Drives the REAL use-cases/handlers against a REAL Postgres (testcontainers) +
REAL GreedySolver. Only the Telegram transport and the LLM are out of the loop
(intents are constructed directly / via the capture use-case). This proves the
chat -> use-case -> repo -> solver -> DB chain that the unit suite mocks away.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from planner.app.capture_task import CaptureTaskUseCase
from planner.app.confirm_plan import ConfirmPlanUseCase
from planner.app.ports import PersonRecord
from planner.bot.handlers.task_router import build_add_project_reply
from planner.domain.calendar.rules import WeekendCalendar
from planner.domain.intent import AddProjectIntent, CaptureTaskIntent
from planner.domain.solver.greedy import GreedySolver
from planner.infra.db.models import (
    Person,
    Template,
    TemplateTask,
    TemplateTaskAssignee,
)
from planner.infra.db.repo import SqlAlchemyRepo

_TITLE_PREFIX = "ITP-"  # unique marker so teardown can purge only our rows


@pytest.fixture(scope="module")
def repo(db_session_factory):
    return SqlAlchemyRepo(db_session_factory)


@pytest_asyncio.fixture
async def andrey(db_session_factory):
    """One active admin person, allowed to take the template task."""
    pid = uuid4()
    async with db_session_factory() as s, s.begin():
        s.add(Person(id=pid, name=f"{_TITLE_PREFIX}Андрей", is_admin=True,
                     is_active=True, capacity_h=8, tg_user_id=970001))
    yield PersonRecord(pid, f"{_TITLE_PREFIX}Андрей", is_admin=True)
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM people WHERE id = :id"), {"id": pid})


@pytest_asyncio.fixture
async def standard_template(db_session_factory, andrey):
    """A 'standard' template with one task assigned to Андрей."""
    tmpl_id, task_id = uuid4(), uuid4()
    async with db_session_factory() as s, s.begin():
        s.add(Template(id=tmpl_id, code="standard", name=f"{_TITLE_PREFIX}Standard"))
        s.add(TemplateTask(id=task_id, template_id=tmpl_id, ord=1,
                           name=f"{_TITLE_PREFIX}Разработка", duration_hours=16))
        await s.flush()  # persist template_task before its FK-referencing assignee
        s.add(TemplateTaskAssignee(template_task_id=task_id,
                                   person_id=andrey.id, strictness="A"))
    yield tmpl_id
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM template_task_assignees "
                             "WHERE template_task_id = :t"), {"t": task_id})
        await s.execute(text("DELETE FROM template_tasks WHERE id = :t"), {"t": task_id})
        await s.execute(text("DELETE FROM templates WHERE id = :t"), {"t": tmpl_id})


@pytest_asyncio.fixture
async def purge_projects(db_session_factory):
    """Delete every project (and its children) created under the test prefix."""
    yield
    async with db_session_factory() as s, s.begin():
        rows = (await s.execute(
            text("SELECT id FROM projects WHERE title LIKE :p"),
            {"p": f"{_TITLE_PREFIX}%"},
        )).all()
        ids = [r[0] for r in rows]
        for pid in ids:
            await s.execute(text("DELETE FROM assignments WHERE task_id IN "
                                 "(SELECT id FROM tasks WHERE project_id = :p)"), {"p": pid})
            await s.execute(text("DELETE FROM tasks WHERE project_id = :p"), {"p": pid})
            await s.execute(text("DELETE FROM plan_versions WHERE project_id = :p"), {"p": pid})
        if ids:
            await s.execute(text("DELETE FROM projects WHERE title LIKE :p"),
                            {"p": f"{_TITLE_PREFIX}%"})
        await s.execute(text("DELETE FROM audit_log WHERE action IN "
                             "('capture_task','confirm_plan','add_project') "
                             "AND created_at > now() - interval '1 hour'"))


async def _scalar(db_session_factory, sql: str, **params):
    async with db_session_factory() as s:
        return (await s.execute(text(sql), params)).first()


@pytest.mark.asyncio
async def test_capture_task_persists_to_real_db(repo, andrey, db_session_factory, purge_projects):
    """Capture use-case -> real tasks + audit rows in Postgres."""
    project_name = f"{_TITLE_PREFIX}МТС"
    intent = CaptureTaskIntent(
        task_title=f"{_TITLE_PREFIX}подготовить бриф",
        project_name=project_name,
        est_hours=4,
    )

    result = await CaptureTaskUseCase(repo).execute(intent, andrey)

    assert result.task_title == f"{_TITLE_PREFIX}подготовить бриф"
    assert result.project_title == project_name

    row = await _scalar(
        db_session_factory,
        "SELECT name, source FROM tasks WHERE name = :n",
        n=f"{_TITLE_PREFIX}подготовить бриф",
    )
    assert row is not None, "captured task not found in DB"
    assert row[1] == "bot_formed"

    audit = await _scalar(
        db_session_factory,
        "SELECT action FROM audit_log WHERE action = 'capture_task' "
        "AND actor_id = :a ORDER BY created_at DESC LIMIT 1",
        a=andrey.id,
    )
    assert audit is not None, "capture_task audit row missing"


@pytest.mark.asyncio
async def test_add_project_proposes_then_confirm_commits(
    repo, andrey, standard_template, db_session_factory, purge_projects
):
    """build_add_project_reply -> proposed plan_version in DB; confirm -> committed."""
    solver = GreedySolver(WeekendCalendar())
    today = date(2026, 6, 2)
    intent = AddProjectIntent(
        title=f"{_TITLE_PREFIX}Альфа",
        template_code="standard",
        deadline=today + timedelta(days=30),
    )

    reply, pv_id = await build_add_project_reply(
        intent, repo=repo, solver=solver, actor_record=andrey, today=today,
    )
    assert f"{_TITLE_PREFIX}Альфа" in reply
    assert pv_id is not None, "no plan version produced"

    proposed = await _scalar(
        db_session_factory,
        "SELECT status FROM plan_versions WHERE id = :id", id=pv_id,
    )
    assert proposed is not None and proposed[0] == "proposed"

    committed = await ConfirmPlanUseCase(repo).execute(pv_id, andrey)
    assert committed.status == "committed"

    row = await _scalar(
        db_session_factory,
        "SELECT status FROM plan_versions WHERE id = :id", id=pv_id,
    )
    assert row[0] == "committed", "plan_version not committed in DB"

    audit = await _scalar(
        db_session_factory,
        "SELECT action FROM audit_log WHERE action = 'confirm_plan' "
        "AND entity_id = :e LIMIT 1",
        e=pv_id,
    )
    assert audit is not None, "confirm_plan audit row missing"
