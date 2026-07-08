"""Unit tests for CaptureTaskUseCase (chat → DB task capture)."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from planner.app.capture_task import INBOX_PROJECT, CaptureTaskUseCase
from planner.app.ports import PersonRecord, ProjectRecord, TaskRecord
from planner.domain.intent import CaptureTaskIntent


class _FakeRepo:
    def __init__(self, *, known_projects=None, known_people=None, known_tasks=None) -> None:
        self._projects = {p.lower(): pr for p, pr in (known_projects or {}).items()}
        self._people = known_people or {}
        self._tasks = known_tasks or {}  # project_id -> list[TaskRecord]
        self.created_projects: list[str] = []
        self.created_tasks: list[dict[str, Any]] = []
        self.assignments: list[tuple] = []
        self.audits: list[tuple] = []

    async def get_project_by_title(self, title: str) -> ProjectRecord | None:
        return self._projects.get(title.lower())

    async def create_project(self, *, title, template_code, deadline,
                             brief_return_date, actor_id) -> ProjectRecord:
        self.created_projects.append(title)
        rec = ProjectRecord(uuid4(), title, "planning", deadline)
        self._projects[title.lower()] = rec
        return rec

    async def create_task(self, *, project_id, name, duration_hours,
                          deadline, actor_id, required_skills=None,
                          time_start=None) -> TaskRecord:
        self.created_tasks.append(
            {
                "project_id": project_id, "name": name, "deadline": deadline,
                "duration_hours": duration_hours,
                "required_skills": required_skills,
                "time_start": time_start,
            }
        )
        return TaskRecord(id=uuid4(), name=name, status="not_done",
                          end_date=deadline, duration_hours=duration_hours)

    async def list_project_tasks(self, project_id) -> list[TaskRecord]:
        return list(self._tasks.get(project_id, []))

    async def get_person_by_name(self, name: str) -> PersonRecord | None:
        return self._people.get(name)

    async def assign_task(self, task_id, person_id, hours) -> None:
        self.assignments.append((task_id, person_id, hours))

    async def add_audit(self, actor_id, action, entity_type, entity_id, payload) -> None:
        self.audits.append((action, entity_type, payload))


_ACTOR = PersonRecord(id=uuid4(), name="Егор", is_admin=False)


@pytest.mark.asyncio
async def test_capture_into_existing_project_with_assignee():
    andrey = PersonRecord(id=uuid4(), name="Андрей")
    mts = ProjectRecord(uuid4(), "МТС", "planning")
    repo = _FakeRepo(known_projects={"МТС": mts}, known_people={"Андрей": andrey})
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(
        task_title="подготовить бриф", assignee_names=["Андрей"],
        project_name="МТС", deadline=date(2026, 6, 16),
    )
    result = await uc.execute(intent, _ACTOR)

    assert result.project_title == "МТС"
    assert result.assignee_names == ["Андрей"]
    assert result.deadline_iso == "2026-06-16"
    assert repo.created_projects == []  # reused existing
    assert repo.created_tasks[0]["project_id"] == mts.id
    assert repo.assignments and repo.assignments[0][1] == andrey.id
    assert repo.audits[0][0] == "capture_task"


@pytest.mark.asyncio
async def test_capture_creates_named_project_when_unknown():
    repo = _FakeRepo()
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="сделать КП", project_name="Билайн")
    result = await uc.execute(intent, _ACTOR)

    assert result.project_title == "Билайн"
    assert repo.created_projects == ["Билайн"]
    assert result.assignee_names == []


@pytest.mark.asyncio
async def test_capture_falls_back_to_inbox_without_project():
    repo = _FakeRepo()
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="позвонить клиенту")
    result = await uc.execute(intent, None)  # actor None path

    assert result.project_title == INBOX_PROJECT
    assert repo.created_projects == [INBOX_PROJECT]
    assert repo.audits[0][2]["assignees"] == []


@pytest.mark.asyncio
async def test_capture_skips_assignment_when_person_unknown():
    repo = _FakeRepo()
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="что-то", assignee_names=["Призрак"])
    result = await uc.execute(intent, _ACTOR)

    assert result.assignee_names == []
    assert repo.assignments == []


@pytest.mark.asyncio
async def test_capture_uses_est_hours_for_duration():
    repo = _FakeRepo()
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="макет", est_hours=12)
    await uc.execute(intent, _ACTOR)

    assert repo.created_tasks[0]["duration_hours"] == 12


@pytest.mark.asyncio
async def test_capture_falls_back_to_default_hours_when_est_none():
    repo = _FakeRepo()
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="макет", est_hours=None)
    await uc.execute(intent, _ACTOR)

    assert repo.created_tasks[0]["duration_hours"] == 8


@pytest.mark.asyncio
async def test_capture_clamps_nonpositive_est_hours_to_default():
    # A hallucinated est_hours=0 (or negative) would create a 0-hour task that
    # corrupts load math; treat it like None and fall back to the default.
    repo = _FakeRepo()
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="макет", est_hours=0)
    await uc.execute(intent, _ACTOR)

    assert repo.created_tasks[0]["duration_hours"] == 8


@pytest.mark.asyncio
async def test_capture_forwards_required_skills():
    repo = _FakeRepo()
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="макет", required_skills=["дизайн"])
    await uc.execute(intent, _ACTOR)

    assert repo.created_tasks[0]["required_skills"] == ["дизайн"]


class _SpySink:
    def __init__(self, *, url: str | None = "https://notion.so/p1") -> None:
        self.url = url
        self.calls: list[Any] = []

    async def push_task(self, task) -> str | None:
        self.calls.append(task)
        return self.url


class _BoomSink:
    async def push_task(self, task) -> str | None:
        raise RuntimeError("notion down")


@pytest.mark.asyncio
async def test_capture_mirrors_to_sink_after_repo_write():
    andrey = PersonRecord(id=uuid4(), name="Андрей")
    mts = ProjectRecord(uuid4(), "МТС", "planning")
    repo = _FakeRepo(known_projects={"МТС": mts}, known_people={"Андрей": andrey})
    sink = _SpySink()
    uc = CaptureTaskUseCase(repo, sink=sink)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(
        task_title="подготовить бриф", assignee_names=["Андрей"],
        project_name="МТС", deadline=date(2026, 6, 20),
    )
    result = await uc.execute(intent, _ACTOR)

    # Sink called exactly once, AFTER the repo write (task already created).
    assert repo.created_tasks  # repo write happened
    assert len(sink.calls) == 1
    pushed = sink.calls[0]
    assert pushed.title == "подготовить бриф"
    assert pushed.assignees == ["Андрей"]
    assert pushed.project == "МТС"
    assert pushed.deadline == date(2026, 6, 20)
    assert result.notion_url == "https://notion.so/p1"


@pytest.mark.asyncio
async def test_capture_without_sink_has_no_notion_url():
    repo = _FakeRepo()
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    result = await uc.execute(CaptureTaskIntent(task_title="x"), _ACTOR)

    assert result.notion_url is None


@pytest.mark.asyncio
async def test_capture_survives_sink_failure():
    repo = _FakeRepo()
    uc = CaptureTaskUseCase(repo, sink=_BoomSink())  # type: ignore[arg-type]

    result = await uc.execute(CaptureTaskIntent(task_title="x"), _ACTOR)

    # Capture still succeeds; no link surfaced.
    assert result.task_title == "x"
    assert result.notion_url is None
    assert repo.created_tasks  # task persisted despite sink failure


# --- dedupe guard: repeated capture of the same task must not duplicate ----


def _project_with_task(title: str, status: str = "not_done"):
    project = ProjectRecord(uuid4(), "Rostic's Молодёжка", "planning")
    task = TaskRecord(id=uuid4(), name=title, status=status,
                      end_date=date(2026, 7, 10), duration_hours=8)
    return project, task


@pytest.mark.asyncio
async def test_capture_skips_duplicate_same_title_same_project():
    project, existing = _project_with_task("Встреча с клиентом")
    repo = _FakeRepo(known_projects={project.title: project},
                     known_tasks={project.id: [existing]})
    sink = _SpySink()
    uc = CaptureTaskUseCase(repo, sink=sink)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="Встреча с клиентом", project_name=project.title)
    result = await uc.execute(intent, _ACTOR)

    assert result.is_duplicate is True
    assert result.task_id == existing.id
    assert result.task_title == existing.name
    assert result.project_title == project.title
    assert repo.created_tasks == []  # no second create
    assert sink.calls == []  # no duplicate Notion card


@pytest.mark.asyncio
async def test_capture_duplicate_check_is_case_and_whitespace_insensitive():
    project, existing = _project_with_task("Встреча с клиентом")
    repo = _FakeRepo(known_projects={project.title: project},
                     known_tasks={project.id: [existing]})
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="  встреча С КЛИЕНТОМ ", project_name=project.title)
    result = await uc.execute(intent, _ACTOR)

    assert result.is_duplicate is True
    assert result.task_id == existing.id
    assert repo.created_tasks == []


@pytest.mark.asyncio
async def test_capture_creates_when_existing_task_is_done():
    project, existing = _project_with_task("Встреча с клиентом", status="done")
    repo = _FakeRepo(known_projects={project.title: project},
                     known_tasks={project.id: [existing]})
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="Встреча с клиентом", project_name=project.title)
    result = await uc.execute(intent, _ACTOR)

    assert result.is_duplicate is False
    assert len(repo.created_tasks) == 1


@pytest.mark.asyncio
async def test_capture_creates_same_title_in_different_project():
    project, existing = _project_with_task("Встреча с клиентом")
    other = ProjectRecord(uuid4(), "МТС", "planning")
    repo = _FakeRepo(known_projects={project.title: project, other.title: other},
                     known_tasks={project.id: [existing]})
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(task_title="Встреча с клиентом", project_name="МТС")
    result = await uc.execute(intent, _ACTOR)

    assert result.is_duplicate is False
    assert len(repo.created_tasks) == 1
    assert repo.created_tasks[0]["project_id"] == other.id


@pytest.mark.asyncio
async def test_capture_assigns_multiple_people():
    andrey = PersonRecord(id=uuid4(), name="Андрей")
    ray = PersonRecord(id=uuid4(), name="Рай")
    repo = _FakeRepo(known_people={"Андрей": andrey, "Рай": ray})
    uc = CaptureTaskUseCase(repo)  # type: ignore[arg-type]

    intent = CaptureTaskIntent(
        task_title="ресёрч по МТС", assignee_names=["Андрей", "Рай"]
    )
    result = await uc.execute(intent, _ACTOR)

    assert result.assignee_names == ["Андрей", "Рай"]
    assert len(repo.assignments) == 2
    assigned_person_ids = {a[1] for a in repo.assignments}
    assert andrey.id in assigned_person_ids
    assert ray.id in assigned_person_ids

@pytest.mark.asyncio
async def test_capture_carries_time_of_day():
    """«встреча в 10:15 на час» — время не теряется (live gap, work chat
    2026-07-08): оно уходит в create_task, в deadline_iso и в Notion-синк."""
    from datetime import date, time

    class _Sink:
        def __init__(self):
            self.pushed = []

        async def push_task(self, task):
            self.pushed.append(task)
            return "https://notion.so/x"

    mts = ProjectRecord(uuid4(), "МТС", "planning")
    repo = _FakeRepo(known_projects={"МТС": mts})
    sink = _Sink()
    uc = CaptureTaskUseCase(repo, sink=sink)  # type: ignore[arg-type]
    intent = CaptureTaskIntent(
        task_title="Встреча с клиентом", project_name="МТС",
        deadline=date(2026, 7, 8), time_start=time(10, 15), est_hours=1,
    )

    result = await uc.execute(intent, _ACTOR)

    assert repo.created_tasks[0]["time_start"] == time(10, 15)
    assert result.deadline_iso == "2026-07-08 10:15"
    assert sink.pushed[0].time_start == time(10, 15)
