"""Opt-in live Notion end-to-end: project → plan → confirm → mirror → done → tick.

Run with: RUN_NOTION_TEST=1 uv run pytest tests/eval/test_notion_e2e_live.py -v
Skipped by default. Requires NOTION_TOKEN + NOTION_DATABASE_ID (integration shared
with the DB; NOTION_PARENT_PAGE_ID optional — falls back to the database) and
Docker (real Postgres via testcontainers, same fixtures as tests/integration).

Creates prefixed pages on the live board; the master card is archived in
teardown, mirrored task rows are left for manual inspection (test board).
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from planner.app.confirm_plan import ConfirmPlanUseCase
from planner.app.mark_task_done import MarkTaskDoneUseCase
from planner.app.ports import PersonRecord
from planner.bot.handlers.task_router import build_add_project_reply
from planner.domain.calendar.rules import WeekendCalendar
from planner.domain.intent import AddProjectIntent
from planner.domain.solver.greedy import GreedySolver
from planner.infra.db.models import (
    Person,
    Template,
    TemplateTask,
    TemplateTaskAssignee,
)
from planner.infra.db.repo import SqlAlchemyRepo

pytestmark = pytest.mark.live

_RUN = (
    os.environ.get("RUN_NOTION_TEST") == "1"
    and bool(os.environ.get("NOTION_TOKEN"))
    and bool(os.environ.get("NOTION_DATABASE_ID"))
)
_PREFIX = f"NLIVE{int(time.time())}-"
_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"


@pytest.fixture(scope="module")
def repo(db_session_factory):
    return SqlAlchemyRepo(db_session_factory)


@pytest_asyncio.fixture
async def manager(db_session_factory):
    pid = uuid4()
    async with db_session_factory() as s, s.begin():
        s.add(Person(id=pid, name=f"{_PREFIX}Менеджер", is_admin=True,
                     is_active=True, capacity_h=8, tg_user_id=970099))
    yield PersonRecord(pid, f"{_PREFIX}Менеджер", is_admin=True)
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM people WHERE id = :id"), {"id": pid})


@pytest_asyncio.fixture
async def standard_template(db_session_factory, manager):
    tmpl_id, task_id = uuid4(), uuid4()
    async with db_session_factory() as s, s.begin():
        s.add(Template(id=tmpl_id, code="standard", name=f"{_PREFIX}Standard"))
        s.add(TemplateTask(id=task_id, template_id=tmpl_id, ord=1,
                           name=f"{_PREFIX}Разработка", duration_hours=8))
        await s.flush()
        s.add(TemplateTaskAssignee(template_task_id=task_id,
                                   person_id=manager.id, strictness="A"))
    yield tmpl_id
    async with db_session_factory() as s, s.begin():
        await s.execute(text("DELETE FROM template_task_assignees "
                             "WHERE template_task_id = :t"), {"t": task_id})
        await s.execute(text("DELETE FROM template_tasks WHERE id = :t"), {"t": task_id})
        await s.execute(text("DELETE FROM templates WHERE id = :t"), {"t": tmpl_id})


@pytest_asyncio.fixture
async def purge_projects(db_session_factory):
    yield
    async with db_session_factory() as s, s.begin():
        rows = (await s.execute(
            text("SELECT id FROM projects WHERE title LIKE :p"), {"p": f"{_PREFIX}%"},
        )).all()
        for (pid,) in rows:
            await s.execute(text("DELETE FROM assignments WHERE task_id IN "
                                 "(SELECT id FROM tasks WHERE project_id = :p)"), {"p": pid})
            await s.execute(text("DELETE FROM tasks WHERE project_id = :p"), {"p": pid})
            await s.execute(text("DELETE FROM plan_versions WHERE project_id = :p"), {"p": pid})
        await s.execute(text("DELETE FROM projects WHERE title LIKE :p"),
                        {"p": f"{_PREFIX}%"})
        # Audit rows reference the test person; purge them before the person
        # fixture teardown deletes it (same pattern as test_bot_pipeline).
        await s.execute(text(
            "DELETE FROM audit_log WHERE action IN "
            "('capture_task','confirm_plan','add_project','mark_task') "
            "AND created_at > now() - interval '1 hour'"
        ))


async def _scalar(db_session_factory, sql: str, **params):
    async with db_session_factory() as s:
        return (await s.execute(text(sql), params)).first()


@pytest.mark.skipif(
    not _RUN, reason="set RUN_NOTION_TEST=1 with NOTION_TOKEN + NOTION_DATABASE_ID"
)
@pytest.mark.asyncio
async def test_full_flow_mirrors_to_live_notion(
    repo, manager, standard_template, db_session_factory, purge_projects
):
    from planner.infra.notion.client import NotionTaskSink
    from planner.infra.notion.project import NotionProjectSink

    token = os.environ["NOTION_TOKEN"]
    db_id = os.environ["NOTION_DATABASE_ID"]
    parent = os.environ.get("NOTION_PARENT_PAGE_ID") or db_id
    project_sink = NotionProjectSink(token, parent)
    task_sink = NotionTaskSink(token, db_id)

    today = date.today()
    intent = AddProjectIntent(
        title=f"{_PREFIX}Пилот",
        template_code="standard",
        deadline=today + timedelta(days=30),
    )

    # 1. Project + proposed plan + Notion master card (strict link rule).
    reply, pv_id = await build_add_project_reply(
        intent, repo=repo, solver=GreedySolver(WeekendCalendar()),
        actor_record=manager, today=today, project_sink=project_sink,
    )
    assert pv_id is not None, reply
    assert "notion" in reply.lower(), f"no Notion link in reply: {reply}"

    card = await _scalar(
        db_session_factory,
        "SELECT id, notion_page_id FROM projects WHERE title = :t",
        t=f"{_PREFIX}Пилот",
    )
    assert card is not None and card[1], "master card id not persisted on the project"
    project_id, card_id = card

    try:
        # 2. Confirm → committed + full mirror onto the board.
        result = await ConfirmPlanUseCase(repo, task_sink).execute(pv_id, manager)
        assert result.plan.status == "committed"
        assert result.mirror_total > 0, "nothing was mirrored"
        assert result.mirror_failed == 0, (
            f"partial mirror: {result.mirror_failed} of {result.mirror_total} failed"
        )

        # 3. Mark done → checkbox ticked on the live master card.
        task_row = await _scalar(
            db_session_factory,
            "SELECT id, name FROM tasks WHERE project_id = :p", p=project_id,
        )
        assert task_row is not None
        await MarkTaskDoneUseCase(repo, project_sink=project_sink).execute(
            task_row[0], manager.id, is_admin=True
        )

        headers = {"Authorization": f"Bearer {token}", "Notion-Version": _VERSION}
        async with httpx.AsyncClient(timeout=15.0) as client:
            blocks = (await client.get(
                f"{_API}/blocks/{card_id}/children", headers=headers
            )).json().get("results", [])
        todos = [
            b for b in blocks
            if b.get("type") == "to_do"
            and task_row[1] in "".join(
                t.get("plain_text", "") for t in b["to_do"].get("rich_text", [])
            )
        ]
        assert todos, f"task «{task_row[1]}» not found on the master card checklist"
        assert todos[0]["to_do"]["checked"] is True, "checkbox was not ticked"
    finally:
        # Keep the live board tidy: archive the master card; mirrored task
        # rows are left on purpose so the run can be inspected by hand.
        await project_sink.archive_card(card_id)
