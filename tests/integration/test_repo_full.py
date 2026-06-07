"""Integration tests covering uncovered repo.py paths (spec 12.1)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from planner.infra.db.models import (
    Person,
    PlanVersion,
    Project,
    Task,
    Template,
    TemplateDependency,
    TemplateTask,
    TemplateTaskAssignee,
)
from planner.infra.db.repo import SqlAlchemyRepo


@pytest.fixture(scope="module")
def repo(db_session_factory):
    return SqlAlchemyRepo(db_session_factory)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def person(db_session_factory):
    pid = uuid4()
    async with db_session_factory() as s, s.begin():
        s.add(Person(id=pid, name="Интег Тест", is_admin=False,
                     is_active=True, capacity_h=8, tg_user_id=99001))
    yield pid
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM people WHERE id = :id"), {"id": pid})


@pytest_asyncio.fixture
async def project(db_session_factory):
    pid = uuid4()
    async with db_session_factory() as s, s.begin():
        s.add(Project(id=pid, title="Интег Проект", status="planning"))
    yield pid
    async with db_session_factory() as s, s.begin():
        await s.execute(
            text("DELETE FROM plan_versions WHERE project_id = :id"), {"id": pid}
        )
        await s.execute(
            text("DELETE FROM tasks WHERE project_id = :id"), {"id": pid}
        )
        await s.execute(text("DELETE FROM projects WHERE id = :id"), {"id": pid})


@pytest_asyncio.fixture
async def committed_plan(db_session_factory, project):
    pv_id = uuid4()
    payload = {"assignments": [{"task_id": str(uuid4()), "person_id": str(uuid4()),
                                "allocations": [{"hours": 8}]}],
               "risks": [], "end_date": None}
    async with db_session_factory() as s, s.begin():
        s.add(PlanVersion(id=pv_id, project_id=project,
                          status="committed", payload=payload))
    yield pv_id, payload
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM plan_versions WHERE id = :id"), {"id": pv_id})


@pytest_asyncio.fixture
async def task_row(db_session_factory, project):
    tid = uuid4()
    async with db_session_factory() as s, s.begin():
        s.add(Task(id=tid, project_id=project, name="Интег задача",
                   duration_hours=8, status="not_done",
                   start_date=date(2026, 6, 10), end_date=date(2026, 6, 11)))
    yield tid
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": tid})


@pytest_asyncio.fixture
async def template(db_session_factory):
    tmpl_id = uuid4()
    tt_id = uuid4()
    async with db_session_factory() as s, s.begin():
        s.add(Template(id=tmpl_id, code="test_tmpl", name="Test Template"))
    async with db_session_factory() as s, s.begin():
        s.add(TemplateTask(id=tt_id, template_id=tmpl_id, ord=1,
                           name="Task A", duration_hours=8))
    yield tmpl_id, tt_id
    async with db_session_factory() as s, s.begin():
        await s.execute(
            text("DELETE FROM template_tasks WHERE template_id = :id"), {"id": tmpl_id}
        )
        await s.execute(text("DELETE FROM templates WHERE id = :id"), {"id": tmpl_id})


# ---------------------------------------------------------------------------
# get_person_by_tg_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_person_by_tg_id_found(repo, person):
    rec = await repo.get_person_by_tg_id(99001)
    assert rec is not None
    assert rec.name == "Интег Тест"


@pytest.mark.asyncio
async def test_get_person_by_tg_id_missing(repo):
    rec = await repo.get_person_by_tg_id(0)
    assert rec is None


# ---------------------------------------------------------------------------
# get_plan_version (None path) + set_plan_version_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_plan_version_missing_returns_none(repo):
    result = await repo.get_plan_version(uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_set_plan_version_status(repo, committed_plan):
    pv_id, _ = committed_plan
    await repo.set_plan_version_status(pv_id, "archived")
    pv = await repo.get_plan_version(pv_id)
    assert pv is not None
    assert pv.status == "archived"


@pytest.mark.asyncio
async def test_set_plan_version_status_missing_id_no_crash(repo):
    # Should silently ignore missing IDs
    await repo.set_plan_version_status(uuid4(), "archived")


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_project_no_template(repo, db_session_factory):
    rec = await repo.create_project(
        title="Созданный Проект",
        template_code="nonexistent",
        deadline=date(2026, 7, 31),
        brief_return_date=None,
        actor_id=None,
    )
    assert rec.title == "Созданный Проект"
    assert rec.status == "planning"
    # cleanup
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM projects WHERE id = :id"), {"id": rec.id})


# ---------------------------------------------------------------------------
# get_committed_plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_committed_plan_found(repo, committed_plan, project):
    pv_id, payload = committed_plan
    result = await repo.get_committed_plan(project)
    assert result is not None
    assert result.status in ("committed", "archived")


@pytest.mark.asyncio
async def test_get_committed_plan_none(repo):
    result = await repo.get_committed_plan(uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# update_task_schedule + set_task_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_task_schedule(repo, task_row):
    await repo.update_task_schedule(
        task_row, date(2026, 6, 15), date(2026, 6, 16), None
    )
    from planner.infra.db.models import Task as TaskModel
    async with repo._sf() as s:
        t = await s.get(TaskModel, task_row)
    assert t is not None
    assert t.start_date == date(2026, 6, 15)
    assert t.end_date == date(2026, 6, 16)


@pytest.mark.asyncio
async def test_update_task_schedule_missing_id_no_crash(repo):
    await repo.update_task_schedule(uuid4(), date(2026, 6, 15), None, None)


@pytest.mark.asyncio
async def test_set_task_status(repo, task_row):
    await repo.set_task_status(task_row, "done")
    from planner.infra.db.models import Task as TaskModel
    async with repo._sf() as s:
        t = await s.get(TaskModel, task_row)
    assert t is not None
    assert t.status == "done"


@pytest.mark.asyncio
async def test_set_task_status_missing_id_no_crash(repo):
    await repo.set_task_status(uuid4(), "done")


# ---------------------------------------------------------------------------
# list_projects + list_project_tasks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_projects_returns_created(repo, project):
    rows = await repo.list_projects()
    ids = [r.id for r in rows]
    assert project in ids


@pytest.mark.asyncio
async def test_list_project_tasks(repo, task_row, project):
    rows = await repo.list_project_tasks(project)
    assert any(t.id == task_row for t in rows)
    assert rows[0].name == "Интег задача"


@pytest.mark.asyncio
async def test_list_project_tasks_empty(repo):
    rows = await repo.list_project_tasks(uuid4())
    assert rows == []


# ---------------------------------------------------------------------------
# list_committed_plans
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_committed_plans_includes_payload(repo, committed_plan):
    pv_id, payload = committed_plan
    plans = await repo.list_committed_plans()
    # At least one plan should be a dict with "assignments" key
    assert any("assignments" in p for p in plans)


# ---------------------------------------------------------------------------
# get_solver_people
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_solver_people_returns_active(repo, person):
    people = await repo.get_solver_people()
    names = [p.name for p in people]
    assert "Интег Тест" in names


# ---------------------------------------------------------------------------
# get_project_template
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_project_template_missing_returns_none(repo):
    result = await repo.get_project_template("nonexistent_xyz")
    assert result is None


@pytest.mark.asyncio
async def test_get_project_template_found(repo, template):
    tmpl_id, tt_id = template
    result = await repo.get_project_template("test_tmpl")
    assert result is not None
    assert result.code == "test_tmpl"
    assert len(result.tasks) == 1
    assert result.tasks[0].name == "Task A"
    assert result.tasks[0].duration_hours == 8


@pytest.mark.asyncio
async def test_get_project_template_with_assignees_and_deps(
    repo, person, db_session_factory
):
    """repo.py:243,251-253 — template with assignee + dep rows are included."""
    tmpl_id, ta_id, tb_id = uuid4(), uuid4(), uuid4()
    async with db_session_factory() as s, s.begin():
        s.add(Template(id=tmpl_id, code="test_tmpl_deps", name="Deps Template"))
    async with db_session_factory() as s, s.begin():
        s.add(TemplateTask(id=ta_id, template_id=tmpl_id, ord=1, name="A", duration_hours=8))
        s.add(TemplateTask(id=tb_id, template_id=tmpl_id, ord=2, name="B", duration_hours=4))
    async with db_session_factory() as s, s.begin():
        s.add(TemplateTaskAssignee(template_task_id=ta_id, person_id=person, strictness="A"))
        s.add(TemplateDependency(template_task_id=tb_id, depends_on_id=ta_id, link_type="FS"))
    try:
        result = await repo.get_project_template("test_tmpl_deps")
        assert result is not None
        assert len(result.tasks) == 2
        task_a = next(t for t in result.tasks if t.name == "A")
        assert person in task_a.allowed_person_ids
        task_b = next(t for t in result.tasks if t.name == "B")
        assert len(task_b.depends_on_ords) == 1
        assert task_b.depends_on_ords[0] == 1  # ord of task A
    finally:
        async with db_session_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM template_dependencies WHERE template_task_id IN (:a,:b)"),
                {"a": tb_id, "b": ta_id},
            )
            await s.execute(
                text("DELETE FROM template_task_assignees WHERE template_task_id = :id"),
                {"id": ta_id},
            )
            await s.execute(
                text("DELETE FROM template_tasks WHERE template_id = :id"), {"id": tmpl_id}
            )
            await s.execute(
                text("DELETE FROM templates WHERE id = :id"), {"id": tmpl_id}
            )


# ---------------------------------------------------------------------------
# list_audit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_audit_returns_entries(repo):
    await repo.add_audit(None, "test_repo_full", "project", None, {"x": 1})
    rows = await repo.list_audit(limit=10)
    actions = [r.action for r in rows]
    assert "test_repo_full" in actions


@pytest.mark.asyncio
async def test_list_audit_pagination(repo):
    rows_page1 = await repo.list_audit(limit=1, offset=0)
    rows_page2 = await repo.list_audit(limit=1, offset=1)
    assert len(rows_page1) <= 1
    assert len(rows_page2) <= 1
