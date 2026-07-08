"""ToolBox unit tests (agentic planner Task 1).

The ToolBox wraps the EXISTING use-cases as Anthropic tool-use tools. Each
executor must return a SHORT Russian string (never raise), dispatch to the
matching use-case, gate write tools behind ``actor['is_admin']``, and keep read
tools open. The Anthropic loop itself is out of scope here — these tests drive
``ToolBox.execute`` directly with fakes.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from planner.app.ports import (
    CapabilityRecord,
    PersonRecord,
    PlanVersionRecord,
    ProjectRecord,
    TaskMeta,
    TaskRecord,
)
from planner.infra.llm.tools import TOOL_SCHEMAS, ToolBox

# --- Tool schema contract -------------------------------------------------

_READ_TOOLS = {"get_team_load", "find_assignees", "list_people", "list_projects"}
_WRITE_TOOLS = {
    "capture_task",
    "plan_project",
    "set_vacation",
    "replan",
    "assign_task",
    "mark_task_done",
}


def test_tool_schemas_cover_every_planned_tool():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert names == _READ_TOOLS | _WRITE_TOOLS


def test_tool_schemas_are_well_formed_anthropic_defs():
    for tool in TOOL_SCHEMAS:
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str) and tool["description"]
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema


# --- Fakes ----------------------------------------------------------------


class _Person:
    def __init__(self, name: str, capacity_h: int = 8) -> None:
        self.id = uuid4()
        self.name = name
        self.capacity_h = capacity_h


class FakeRepo:
    """Async repo double recording calls and returning canned data."""

    async def list_project_tasks(self, project_id):
        return []  # dedupe guard: no pre-existing tasks in these fakes

    def __init__(
        self,
        *,
        people: list[PersonRecord] | None = None,
        solver_people: list[Any] | None = None,
        projects: list[ProjectRecord] | None = None,
        capabilities: tuple[CapabilityRecord, ...] = (),
        committed: list[dict[str, Any]] | None = None,
        tasks_meta: list[TaskMeta] | None = None,
        plan_versions: dict[UUID, PlanVersionRecord] | None = None,
        people_by_name: dict[str, PersonRecord] | None = None,
        template: Any | None = None,
    ) -> None:
        self._people = people or []
        self._solver_people = solver_people or []
        self._projects = projects or []
        self._capabilities = capabilities
        self._committed = committed or []
        self._tasks_meta = tasks_meta or []
        self._plan_versions = plan_versions or {}
        self._people_by_name = people_by_name or {}
        self._template = template
        self.audits: list[tuple] = []
        self.overrides: list[tuple] = []
        self.task_statuses: dict[UUID, str] = {}
        self.reassigned: list[tuple] = []
        self.transitions: list[tuple] = []
        self.created_tasks: list[dict[str, Any]] = []
        self.created_projects: list[str] = []
        self.saved_plans: list[tuple] = []

    async def list_people(self) -> list[PersonRecord]:
        return self._people

    async def list_projects(self) -> list[ProjectRecord]:
        return self._projects

    async def get_solver_people(self) -> tuple[Any, ...]:
        return tuple(self._solver_people)

    async def get_person_capabilities(self) -> tuple[CapabilityRecord, ...]:
        return self._capabilities

    async def list_committed_plans(self) -> list[dict[str, Any]]:
        return self._committed

    async def list_committed_plans_with_project(self):
        return []

    async def get_task_name_map(self) -> dict[UUID, str]:
        return {}

    async def list_task_dependencies(self) -> list[Any]:
        return []

    async def list_day_overrides(self) -> tuple[Any, ...]:
        return ()

    async def get_person_by_name(self, name: str) -> PersonRecord | None:
        return self._people_by_name.get(name)

    async def list_tasks_with_meta(self) -> list[TaskMeta]:
        return self._tasks_meta

    async def set_task_status(self, task_id, status) -> None:
        self.task_statuses[task_id] = status

    async def set_task_assignee(self, task_id, person_id, hours: int = 8) -> bool:
        self.reassigned.append(("set", task_id, person_id))
        return True

    async def reassign_in_plan(self, task_id, new_person_id) -> bool:
        self.reassigned.append(("plan", task_id, new_person_id))
        return True

    async def upsert_day_override(self, person_id, day, capacity_h, reason) -> None:
        self.overrides.append((person_id, day, capacity_h, reason))

    async def get_plan_version(self, pv_id: UUID) -> PlanVersionRecord | None:
        return self._plan_versions.get(pv_id)

    async def transition_plan_status(self, pv_id, from_status, to_status) -> bool:
        self.transitions.append((pv_id, from_status, to_status))
        return pv_id in self._plan_versions

    async def add_audit(self, *args) -> None:
        self.audits.append(args)

    async def get_project_template(self, code: str) -> Any | None:
        return self._template

    async def get_project_by_title(self, title: str) -> ProjectRecord | None:
        for p in self._projects:
            if p.title.casefold() == title.casefold():
                return p
        return None

    async def create_project(self, *, title, template_code, deadline,
                             brief_return_date, actor_id, priority="medium",
                             project_id=None) -> ProjectRecord:
        self.created_projects.append(title)
        return ProjectRecord(project_id or uuid4(), title, "planning", deadline)

    async def create_task(self, *, project_id, name, duration_hours, deadline,
                          actor_id, required_skills=None) -> TaskRecord:
        self.created_tasks.append({"name": name})
        return TaskRecord(id=uuid4(), name=name, status="not_done",
                          end_date=deadline, duration_hours=duration_hours)

    async def assign_task(self, task_id, person_id, hours) -> None:
        pass

    async def save_project_tasks(self, project_id, tasks, assignments) -> None:
        self.saved_plans.append(("tasks", project_id))

    async def save_plan_version(self, project_id, status, payload, actor_id):
        pv = PlanVersionRecord(uuid4(), project_id, status, payload)
        self._plan_versions[pv.id] = pv
        self.saved_plans.append(("version", pv.id, status))
        return pv


class FakeSolver:
    """Solver double returning an empty feasible plan + zero-diff."""

    def plan(self, request):
        from planner.domain.models import PlanResult

        return PlanResult(assignments=(), risks=(), end_date=None)

    def diff(self, base, modified):
        from planner.domain.models import PlanDiff

        return PlanDiff()

    def presented_earliest_end(self, request, today):
        return None


_ADMIN = {"is_admin": True}
_MEMBER = {"is_admin": False}


def _admin_record() -> PersonRecord:
    return PersonRecord(id=uuid4(), name="Менеджер", is_admin=True)


def _box(repo: FakeRepo, *, actor: dict, actor_record: PersonRecord | None = None,
         solver: Any | None = None, sink: Any = None, project_sink: Any = None) -> ToolBox:
    return ToolBox(
        repo=repo,
        solver=solver or FakeSolver(),
        actor=actor,
        actor_record=actor_record if actor_record is not None else _admin_record(),
        task_sink=sink,
        project_sink=project_sink,
    )


# --- Read tools -----------------------------------------------------------


@pytest.mark.asyncio
async def test_list_projects_returns_titles():
    repo = FakeRepo(projects=[
        ProjectRecord(uuid4(), "Альфа", "planning"),
        ProjectRecord(uuid4(), "Бета", "active"),
    ])
    out = await _box(repo, actor=_MEMBER).execute("list_projects", {})
    assert isinstance(out, str)
    assert "Альфа" in out and "Бета" in out


@pytest.mark.asyncio
async def test_list_people_returns_names_for_non_admin():
    # Read tools are open to non-admins (acceptance G).
    repo = FakeRepo(people=[PersonRecord(uuid4(), "Андрей"), PersonRecord(uuid4(), "Дима")])
    out = await _box(repo, actor=_MEMBER).execute("list_people", {})
    assert "Андрей" in out and "Дима" in out


@pytest.mark.asyncio
async def test_get_team_load_reports_used_vs_capacity_in_days():
    andrey = _Person("Андрей", capacity_h=8)
    repo = FakeRepo(
        solver_people=[andrey],
        committed=[{
            "assignments": [{
                "task_id": str(uuid4()),
                "person_id": str(andrey.id),
                "start_date": date.today().isoformat(),
                "end_date": date.today().isoformat(),
                "allocations": [
                    {"person_id": str(andrey.id),
                     "day": (date.today() + timedelta(days=i)).isoformat(),
                     "hours": 8}
                    for i in range(3)
                ],
            }],
        }],
    )
    out = await _box(repo, actor=_MEMBER).execute("get_team_load", {})
    assert "Андрей" in out
    assert "дн" in out.lower()  # reports days, not raw hours


@pytest.mark.asyncio
async def test_find_assignees_ranks_by_skill():
    dima = CapabilityRecord(person_id=uuid4(), name="Дима", skills=frozenset({"дизайн"}))
    oleg = CapabilityRecord(person_id=uuid4(), name="Олег", skills=frozenset({"аналитика"}))
    repo = FakeRepo(capabilities=(dima, oleg))
    out = await _box(repo, actor=_MEMBER).execute(
        "find_assignees", {"required_skills": ["дизайн"]}
    )
    assert "Дима" in out


@pytest.mark.asyncio
async def test_what_if_tool_removed_from_agent():
    """what_if was dropped from the agent (broken diff rendering); levers are
    suggested as text instead."""
    assert "what_if" not in {t["name"] for t in TOOL_SCHEMAS}
    out = await _box(FakeRepo(), actor=_MEMBER).execute("what_if", {"operation": "add_person"})
    assert out == "Неизвестный инструмент what_if."


# --- Admin gate -----------------------------------------------------------


@pytest.mark.parametrize("name,args", [
    ("capture_task", {"title": "позвонить клиенту"}),
    ("plan_project", {"title": "Альфа", "template": "standard"}),
    ("set_vacation", {"person": "Андрей", "day_from": "2026-07-01", "day_to": "2026-07-05"}),
    ("replan", {}),
    ("assign_task", {"task_ref": "дизайн", "person": "Андрей"}),
    ("mark_task_done", {"task_ref": "дизайн"}),
])
@pytest.mark.asyncio
async def test_write_tools_blocked_for_non_admin(name, args):
    repo = FakeRepo()
    out = await _box(repo, actor=_MEMBER, actor_record=None).execute(name, args)
    assert out == "Только админ может менять план."
    # Gate must short-circuit before any write reaches the repo.
    assert repo.audits == []
    assert repo.created_tasks == []
    assert repo.overrides == []
    assert repo.task_statuses == {}


# --- Write tools (admin) --------------------------------------------------


_FULL_TASK = {
    "title": "добрифовать МТС",
    "assignees": ["Андрей"],
    "project": "МТС",
    "deadline": "2026-06-20",
}


@pytest.mark.asyncio
async def test_capture_task_dispatches_and_returns_string():
    repo = FakeRepo()
    out = await _box(repo, actor=_ADMIN).execute("capture_task", dict(_FULL_TASK))
    assert isinstance(out, str)
    assert repo.created_tasks and repo.created_tasks[0]["name"] == "добрифовать МТС"


@pytest.mark.asyncio
async def test_capture_task_missing_key_fields_signals_clarify_and_skips_write():
    """Missing key field → stash partial args for the button clarify; never write."""
    repo = FakeRepo()
    box = _box(repo, actor=_ADMIN)
    await box.execute("capture_task", {"title": "добрифовать МТС"})
    assert box.pending_capture is not None
    assert box.pending_capture["title"] == "добрифовать МТС"
    assert box.pending_capture["project"] == "" and box.pending_capture["assignees"] == []
    assert box.pending_capture["deadline"] is None
    assert repo.created_tasks == []
    assert box.captured_notion_urls == []


@pytest.mark.asyncio
async def test_capture_task_partial_args_preserved_for_clarify():
    """Only deadline missing → known fields ride along in pending_capture."""
    repo = FakeRepo()
    box = _box(repo, actor=_ADMIN)
    await box.execute(
        "capture_task", {"title": "КП", "assignees": ["Андрей"], "project": "МТС"}
    )
    assert box.pending_capture is not None
    assert box.pending_capture["project"] == "МТС"
    assert box.pending_capture["assignees"] == ["Андрей"]
    assert box.pending_capture["deadline"] is None
    assert repo.created_tasks == []


@pytest.mark.asyncio
async def test_capture_task_records_notion_url():
    """A successful Notion push is stashed for the bot to surface deterministically."""
    class _Sink:
        async def push_task(self, task):
            return "https://notion.so/page-1"

    repo = FakeRepo()
    box = _box(repo, actor=_ADMIN, sink=_Sink())
    await box.execute("capture_task", dict(_FULL_TASK))
    assert box.captured_notion_urls == ["https://notion.so/page-1"]


@pytest.mark.asyncio
async def test_capture_same_title_merges_into_one_task():
    """Two assignees on one task → one DB task with both, not duplicate tasks."""
    andrey = PersonRecord(uuid4(), "Андрей")
    rai = PersonRecord(uuid4(), "Рай")
    repo = FakeRepo(people_by_name={"Андрей": andrey, "Рай": rai})
    box = _box(repo, actor=_ADMIN)
    base = {"title": "Дабриф по МТС", "project": "МТС", "deadline": "2026-06-23"}
    await box.execute("capture_task", {**base, "assignees": ["Андрей"]})
    await box.execute("capture_task", {**base, "assignees": ["Рай"]})
    assert len(repo.created_tasks) == 1            # one task, not two
    assert len(box.captured_replies) == 1
    assert "Андрей" in box.captured_replies[0] and "Рай" in box.captured_replies[0]


@pytest.mark.asyncio
async def test_set_vacation_dispatches_to_use_case():
    andrey = PersonRecord(uuid4(), "Андрей")
    repo = FakeRepo(people_by_name={"Андрей": andrey})
    out = await _box(repo, actor=_ADMIN).execute(
        "set_vacation",
        {"person": "Андрей", "day_from": "2026-07-01", "day_to": "2026-07-03"},
    )
    assert isinstance(out, str)
    assert len(repo.overrides) == 3  # one upsert per day in the range


@pytest.mark.asyncio
async def test_assign_task_dispatches_to_reassign():
    andrey = PersonRecord(uuid4(), "Андрей")
    tid = uuid4()
    repo = FakeRepo(
        people_by_name={"Андрей": andrey},
        tasks_meta=[TaskMeta(
            task_id=tid, task_name="дизайн", project_title="Альфа",
            priority="medium", status="not_done", start_date=None, end_date=None,
            duration_hours=8, assignee_id=None, assignee_name=None, deadline=None,
        )],
    )
    out = await _box(repo, actor=_ADMIN).execute(
        "assign_task", {"task_ref": "дизайн", "person": "Андрей"}
    )
    assert "Андрей" in out
    assert any(kind == "set" for kind, *_ in repo.reassigned)


@pytest.mark.asyncio
async def test_replan_dispatches_and_returns_string():
    repo = FakeRepo()  # nothing committed → friendly "nothing to replan"
    out = await _box(repo, actor=_ADMIN).execute("replan", {})
    assert isinstance(out, str) and out


@pytest.mark.asyncio
async def test_plan_project_proposes_and_stashes_pv_id():
    from planner.app.add_project import ProjectTemplate, TemplateTaskSpec

    person = _Person("Андрей")
    template = ProjectTemplate(
        code="standard",
        tasks=(TemplateTaskSpec(ord=1, name="дизайн", duration_hours=8,
                                allowed_person_ids=(person.id,)),),
    )
    repo = FakeRepo(solver_people=[person], template=template)
    box = _box(repo, actor=_ADMIN)
    out = await box.execute("plan_project", {"title": "Альфа", "template": "standard"})
    assert isinstance(out, str)
    assert box.last_proposed_pv_id is not None
    assert any(kind == "version" for kind, *_ in repo.saved_plans)


def _task_meta(tid: UUID, name: str, project_title: str = "Альфа") -> TaskMeta:
    return TaskMeta(
        task_id=tid, task_name=name, project_title=project_title,
        priority="medium", status="not_done", start_date=None, end_date=None,
        duration_hours=8, assignee_id=None, assignee_name=None, deadline=None,
    )


class _RecordingProjectSink:
    def __init__(self) -> None:
        self.ticked: list[tuple[str, str]] = []

    async def mark_task_done(self, page_id: str, task_name: str) -> bool:
        self.ticked.append((page_id, task_name))
        return True


@pytest.mark.asyncio
async def test_mark_task_done_sets_status_and_ticks_notion():
    tid = uuid4()
    repo = FakeRepo(
        tasks_meta=[_task_meta(tid, "дизайн")],
        projects=[ProjectRecord(uuid4(), "Альфа", "active", notion_page_id="page-1")],
    )
    sink = _RecordingProjectSink()

    out = await _box(repo, actor=_ADMIN, project_sink=sink).execute(
        "mark_task_done", {"task_ref": "дизайн"}
    )

    assert "дизайн" in out
    assert repo.task_statuses[tid] == "done"
    assert sink.ticked == [("page-1", "дизайн")]


@pytest.mark.asyncio
async def test_mark_task_done_unknown_task_returns_clean_error():
    repo = FakeRepo()
    out = await _box(repo, actor=_ADMIN).execute("mark_task_done", {"task_ref": "нет такой"})
    assert out == "Не нашёл задачу «нет такой» — уточни название."
    assert repo.task_statuses == {}


@pytest.mark.asyncio
async def test_mark_task_done_ambiguous_asks_to_clarify():
    repo = FakeRepo(
        tasks_meta=[_task_meta(uuid4(), "дизайн"), _task_meta(uuid4(), "текст")],
    )
    out = await _box(repo, actor=_ADMIN).execute(
        "mark_task_done", {"task_ref": "дизайн и текст"}
    )
    assert "уточни" in out
    assert repo.task_statuses == {}


@pytest.mark.asyncio
async def test_agent_has_no_confirm_tool_commit_is_manager_gated():
    """Committing is manager-only (inline ✅ button / typed «ок»). The agent must
    NOT be able to commit a plan it just proposed (was auto-committing)."""
    assert "confirm_plan" not in {t["name"] for t in TOOL_SCHEMAS}
    out = await _box(FakeRepo(), actor=_ADMIN).execute("confirm_plan", {})
    assert out == "Неизвестный инструмент confirm_plan."


# --- Error handling + unknown tool ---------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_string():
    out = await _box(FakeRepo(), actor=_ADMIN).execute("does_not_exist", {})
    assert out == "Неизвестный инструмент does_not_exist."


@pytest.mark.asyncio
async def test_executor_exception_is_caught_as_error_string():
    class Boom(FakeRepo):
        async def list_projects(self):
            raise RuntimeError("db down")

    out = await _box(Boom(), actor=_MEMBER).execute("list_projects", {})
    assert out.startswith("Ошибка инструмента list_projects:")
