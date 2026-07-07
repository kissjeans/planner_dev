"""Agent ToolBox + Anthropic tool schemas (agentic planner Task 1).

The tool-use agent reads/reasons over the DB and acts through these tools, each
a THIN wrapper over an EXISTING use-case (no business logic is reimplemented
here). ``ToolBox.execute`` dispatches by name and ALWAYS returns a short Russian
string — including a clear error string the model can react to. It never raises.

Guardrails (spec section 13/16/21):
- Write tools (capture_task / plan_project / set_vacation / replan / assign_task /
  mark_task_done) require ``actor['is_admin']``; otherwise they return
  «Только админ может менять план.» without touching the repo.
- ``plan_project`` only PROPOSES a plan version. Committing is manager-gated and
  deterministic: the agent has NO confirm tool — the manager presses the inline
  ✅ button (or types «ок»). The proposed id is stashed on
  ``self.last_proposed_pv_id`` so the caller can attach the ✅/✏️ buttons.
- Read tools stay open to everyone (acceptance G).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any
from uuid import UUID

import structlog

from planner.app.ports import PersonRecord, ProjectSinkPort, RepoPort, TaskSinkPort
from planner.app.suggest_assignees import SuggestAssigneesUseCase
from planner.domain.solver.ports import SolverPort
from planner.domain.units import hours_to_working_days

log = structlog.get_logger(__name__)

_ADMIN_ONLY_MSG = "Только админ может менять план."
_WRITE_TOOLS = frozenset(
    {"capture_task", "plan_project", "set_vacation", "replan", "assign_task", "mark_task_done"}
)
_LOAD_DAYS = 14
_MAX_LISTED = 12  # cap list output so the agent context stays small

# --- Anthropic tool schemas (one per the plan's tool table) ----------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_team_load",
        "description": (
            "Загрузка команды: занято против ёмкости в рабочих днях за горизонт. "
            "Можно сузить до одного человека (person_name) или задать число дней."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person_name": {"type": "string", "description": "Имя человека (опц.)"},
                "days": {"type": "integer", "description": "Горизонт в днях (по умолчанию 14)"},
            },
        },
    },
    {
        "name": "find_assignees",
        "description": (
            "Подобрать исполнителей по требуемым навыкам, ранжируя по покрытию "
            "навыков и текущей загрузке. Только подсказка — не назначает. "
            "Если у задачи есть жёсткая привязка из шаблона — передай имена в "
            "bound_assignees: тогда выбор идёт ТОЛЬКО из них, навыки лишь "
            "упорядочивают внутри привязки."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "required_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список требуемых навыков",
                },
                "bound_assignees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Имена жёстко привязанных исполнителей (из шаблона). "
                        "Если заданы — кандидаты ограничены только ими."
                    ),
                },
            },
            "required": ["required_skills"],
        },
    },
    {
        "name": "list_people",
        "description": "Список команды: имена, навыки и текущая загрузка в днях.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_projects",
        "description": "Список проектов с их статусами и дедлайнами.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "capture_task",
        "description": (
            "Записать задачу в БД (низкофрикционный путь). Можно указать "
            "исполнителей, проект, дедлайн, оценку часов и требуемые навыки. "
            "Пустые поля остаются пустыми — ничего не переспрашивается."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "assignees": {"type": "array", "items": {"type": "string"}},
                "project": {"type": "string"},
                "deadline": {"type": "string", "description": "YYYY-MM-DD"},
                "est_hours": {"type": "integer"},
                "required_skills": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title"],
        },
    },
    {
        "name": "plan_project",
        "description": (
            "Создать проект из шаблона (standard|lite) и ПРЕДЛОЖИТЬ план "
            "(статус proposed). Менеджер подтверждает кнопкой ✅ Подтвердить. "
            "Без дедлайна — обратный режим (solver считает раннюю дату + буфер)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "template": {"type": "string", "enum": ["standard", "lite"]},
                "deadline": {"type": "string", "description": "YYYY-MM-DD (опц.)"},
            },
            "required": ["title", "template"],
        },
    },
    {
        "name": "set_vacation",
        "description": (
            "Оформить отпуск/недоступность человека на диапазон дат "
            "(capacity_h=0 — полный выходной). После — обычно replan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person": {"type": "string"},
                "day_from": {"type": "string", "description": "YYYY-MM-DD"},
                "day_to": {"type": "string", "description": "YYYY-MM-DD"},
                "capacity_h": {"type": "integer", "description": "0 — полный выходной"},
            },
            "required": ["person", "day_from", "day_to"],
        },
    },
    {
        "name": "replan",
        "description": (
            "Пересчитать зафиксированные планы с текущими ограничениями "
            "(отпуска и т.п.) и показать перегрузки. План не перезаписывается."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "assign_task",
        "description": "Назначить/переназначить существующую задачу на человека.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_ref": {"type": "string", "description": "Название задачи"},
                "person": {"type": "string"},
            },
            "required": ["task_ref", "person"],
        },
    },
    {
        "name": "mark_task_done",
        "description": (
            "Отметить задачу выполненной, когда пишут «задача готова / сделана / "
            "выполнена / закрыта». Ставит статус done — при replan задача больше "
            "не переносится, а в чек-листе проекта в Notion ставится галочка."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_ref": {"type": "string", "description": "Название задачи"},
            },
            "required": ["task_ref"],
        },
    },
]


def _opt_date(v: object) -> date | None:
    """Parse an optional ISO date arg from the model, tolerating None/blank."""
    if not v:
        return None
    return date.fromisoformat(str(v))


def _req_date(v: object) -> date:
    """Parse a required ISO date arg (raises → caught by ToolBox.execute)."""
    return date.fromisoformat(str(v))


class ToolBox:
    """Dispatches Anthropic tool calls to the existing use-cases.

    ``actor`` is the request-scoped actor dict (carries ``is_admin``);
    ``actor_record`` is the resolved :class:`PersonRecord` (or None for an
    unknown sender). ``task_sink`` mirrors captured tasks to Notion (best-effort).
    """

    def __init__(
        self,
        *,
        repo: RepoPort,
        solver: SolverPort,
        actor: dict[str, Any],
        actor_record: PersonRecord | None,
        task_sink: TaskSinkPort | None = None,
        project_sink: ProjectSinkPort | None = None,
    ) -> None:
        self._repo = repo
        self._solver = solver
        self._actor = actor
        self._actor_record = actor_record
        self._sink = task_sink
        self._project_sink = project_sink
        self.last_proposed_pv_id: UUID | None = None
        # Notion URLs of tasks captured this request, surfaced deterministically
        # by the bot — the model paraphrases tool output and drops links.
        self.captured_notion_urls: list[str] = []
        # Partial task args when capture is missing a key field — the bot drives a
        # deterministic button clarify instead of trusting free-text follow-ups.
        self.pending_capture: dict[str, Any] | None = None
        # Deterministic capture confirmations shown verbatim by the bot (not the
        # model's narration, which mangles layout). Same-title captures within a
        # turn merge into ONE task (several assignees, not duplicate tasks).
        self.captured_replies: list[str] = []
        self._captures_by_key: dict[str, dict[str, Any]] = {}

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        if name in _WRITE_TOOLS and not self._actor.get("is_admin"):
            return _ADMIN_ONLY_MSG
        executor = _EXECUTORS.get(name)
        if executor is None:
            return f"Неизвестный инструмент {name}."
        try:
            return await executor(self, args or {})
        except Exception as exc:  # noqa: BLE001 — tools never raise into the loop
            log.warning("tool_failed", tool=name, error=str(exc))
            return f"Ошибка инструмента {name}: {exc}"

    # --- Read tools -------------------------------------------------------

    async def _get_team_load(self, args: dict[str, Any]) -> str:
        people = list(await self._repo.get_solver_people())
        if not people:
            return "В команде нет активных людей."
        wanted = (args.get("person_name") or "").strip().casefold()
        if wanted:
            people = [p for p in people if p.name.casefold() == wanted] or people
        used = await self._committed_hours()
        lines = ["Загрузка команды (занято / ёмкость, рабочие дни):"]
        for p in people:
            used_days = hours_to_working_days(used.get(p.id, 0), p.capacity_h)
            cap_days = hours_to_working_days(p.capacity_h * _LOAD_DAYS, p.capacity_h)
            lines.append(f"• {p.name}: {used_days} / {cap_days} дн.")
        return "\n".join(lines)

    async def _find_assignees(self, args: dict[str, Any]) -> str:
        skills = [str(s) for s in (args.get("required_skills") or [])]
        bound_names = [str(n) for n in (args.get("bound_assignees") or [])]
        restrict_to: frozenset[UUID] | None = None
        header = "Кандидаты:"
        if bound_names:
            by_name = {p.name.casefold(): p.id for p in await self._repo.list_people()}
            ids = frozenset(
                by_name[n.casefold()] for n in bound_names if n.casefold() in by_name
            )
            if ids:
                restrict_to = ids
                header = "Кандидаты (жёсткая привязка из шаблона):"
        suggestions = await SuggestAssigneesUseCase(self._repo).execute(
            skills, restrict_to=restrict_to
        )
        ranked = [s for s in suggestions if s.coverage > 0] or list(suggestions)
        if not ranked:
            return "Подходящих исполнителей не нашёл."
        lines = [header]
        for s in ranked[:_MAX_LISTED]:
            lines.append(
                f"• {s.name}: покрытие {int(s.coverage * 100)}%, загрузка {s.load_hours} ч."
            )
        return "\n".join(lines)

    async def _list_people(self, args: dict[str, Any]) -> str:
        people = await self._repo.list_people()
        if not people:
            return "Команда пуста."
        caps = {c.person_id: c.skills for c in await self._repo.get_person_capabilities()}
        used = await self._committed_hours()
        lines = ["Команда:"]
        for p in people[:_MAX_LISTED]:
            skills = ", ".join(sorted(caps.get(p.id, frozenset()))) or "—"
            load_days = hours_to_working_days(used.get(p.id, 0), p.capacity_h)
            role = f", {p.role_label}" if p.role_label else ""
            lines.append(f"• {p.name}{role} [{skills}] — загрузка {load_days} дн.")
        return "\n".join(lines)

    async def _list_projects(self, args: dict[str, Any]) -> str:
        projects = await self._repo.list_projects()
        if not projects:
            return "Проектов нет."
        lines = ["Проекты:"]
        for pr in projects[:_MAX_LISTED]:
            when = pr.deadline.isoformat() if pr.deadline else "без дедлайна"
            lines.append(f"• {pr.title} ({pr.status}, дедлайн {when})")
        return "\n".join(lines)

    # --- Write tools (admin-gated in execute) -----------------------------

    async def _capture_task(self, args: dict[str, Any]) -> str:
        from planner.bot.handlers.task_router import (
            build_capture_reply,
            format_capture_confirmation,
        )
        from planner.domain.intent import CaptureTaskIntent

        # Clarify ONLY the three key fields when missing (исполнитель / проект /
        # дедлайн) — never invent them or default to "Inbox", and never ask about
        # hours/skills. One consolidated ask, then the model re-calls with answers.
        assignees = [str(a) for a in (args.get("assignees") or [])]
        project = (args.get("project") or "").strip()
        deadline = _opt_date(args.get("deadline"))
        missing = []
        if not assignees:
            missing.append("кто исполнитель")
        if not project:
            missing.append("какой проект")
        if deadline is None:
            missing.append("дедлайн")
        if missing:
            self.pending_capture = {
                "title": (args.get("title") or "задача").strip(),
                "assignees": assignees,
                "project": project,
                "deadline": deadline.isoformat() if deadline else None,
                "est_hours": args.get("est_hours"),
                "required_skills": [str(s) for s in (args.get("required_skills") or [])],
            }
            return (
                "Недостающие ключевые поля запрошены у менеджера кнопками — "
                "задача будет поставлена после выбора, отвечать ничего не нужно."
            )
        # One task on several assignees: the model may call capture_task once per
        # person — merge same-title calls into the first task instead of duplicating.
        key = " ".join(str(args["title"]).split()).casefold()
        if key in self._captures_by_key:
            entry = self._captures_by_key[key]
            for name in assignees:
                person = await self._repo.get_person_by_name(name)
                if person is not None and person.name not in entry["assignees"]:
                    await self._repo.assign_task(
                        entry["task_id"], person.id, entry["duration"]
                    )
                    entry["assignees"].append(person.name)
            self.captured_replies[entry["idx"]] = format_capture_confirmation(
                title=entry["title"], project=entry["project"],
                assignees=entry["assignees"], deadline_iso=entry["deadline_iso"],
            )
            return "Это та же задача — добавил исполнителя, новую не создаю."

        intent = CaptureTaskIntent(
            task_title=args["title"],
            assignee_names=assignees,
            project_name=project,
            deadline=deadline,
            est_hours=args.get("est_hours"),
            required_skills=[str(s) for s in (args.get("required_skills") or [])],
        )
        _text, result = await build_capture_reply(
            intent, repo=self._repo, actor_record=self._actor_record, task_sink=self._sink
        )
        if result.notion_url:
            self.captured_notion_urls.append(result.notion_url)
        confirm = format_capture_confirmation(
            title=result.task_title, project=result.project_title,
            assignees=result.assignee_names, deadline_iso=result.deadline_iso,
        )
        self.captured_replies.append(confirm)
        self._captures_by_key[key] = {
            "task_id": result.task_id, "duration": result.duration_hours,
            "title": result.task_title, "project": result.project_title,
            "deadline_iso": result.deadline_iso,
            "assignees": list(result.assignee_names), "idx": len(self.captured_replies) - 1,
        }
        return confirm

    async def _plan_project(self, args: dict[str, Any]) -> str:
        from planner.bot.handlers.task_router import build_add_project_reply
        from planner.domain.intent import AddProjectIntent

        # Any chat participant can create a project — an unlinked author is fine
        # (actor_id stays None). The write-gate already ran in execute().
        intent = AddProjectIntent(
            title=args["title"],
            template_code=args["template"],
            deadline=_opt_date(args.get("deadline")),
        )
        text, pv_id = await build_add_project_reply(
            intent,
            repo=self._repo,
            solver=self._solver,
            actor_record=self._actor_record,
            today=date.today(),
            project_sink=self._project_sink,
            # Collect the Notion link so the bot re-attaches it deterministically
            # even if the model paraphrases the reply (strict rule).
            notion_links=self.captured_notion_urls,
        )
        self.last_proposed_pv_id = pv_id
        return text

    async def _set_vacation(self, args: dict[str, Any]) -> str:
        from planner.app.set_vacation import PersonNotFoundError, SetVacationUseCase
        from planner.domain.intent import VacationIntent

        intent = VacationIntent(
            person_name=args["person"],
            day_from=_req_date(args["day_from"]),
            day_to=_req_date(args["day_to"]),
            capacity_h=int(args.get("capacity_h", 0)),
        )
        actor_id = self._actor_record.id if self._actor_record else None
        try:
            days = await SetVacationUseCase(self._repo).execute(
                intent, actor_id, is_admin=True
            )
        except PersonNotFoundError:
            return f"Не нашёл человека «{intent.person_name}» — уточни имя."
        return (
            f"Оформил отпуск {intent.person_name}: "
            f"{intent.day_from}–{intent.day_to} ({days} дн.). Запусти replan при необходимости."
        )

    async def _replan(self, args: dict[str, Any]) -> str:
        from planner.bot.handlers.replan import build_replan_summary

        return await build_replan_summary(self._repo, self._solver, today=date.today())

    async def _assign_task(self, args: dict[str, Any]) -> str:
        from planner.bot.handlers.task_router import build_assign_reply
        from planner.domain.intent import AssignIntent

        intent = AssignIntent(task_ref=args["task_ref"], person_name=args["person"])
        actor_id = self._actor_record.id if self._actor_record else None
        return await build_assign_reply(intent, repo=self._repo, actor_id=actor_id)

    async def _mark_task_done(self, args: dict[str, Any]) -> str:
        from planner.app.mark_task_done import MarkTaskDoneUseCase
        from planner.bot.handlers.task_router import _match_tasks

        task_ref = str(args.get("task_ref") or "").strip()
        candidates = _match_tasks(task_ref, await self._repo.list_tasks_with_meta())
        if not candidates:
            return f"Не нашёл задачу «{task_ref}» — уточни название."
        if len(candidates) > 1:
            names = ", ".join(sorted({t.task_name for t in candidates}))
            return f"Нашёл несколько задач ({names}) — уточни, какую отметить."

        task = candidates[0]
        actor_id = self._actor_record.id if self._actor_record else None
        # The write-gate already ran in execute(); the Notion tick (if configured)
        # happens inside the use-case — keyless degrade, best-effort.
        await MarkTaskDoneUseCase(self._repo, project_sink=self._project_sink).execute(
            task.task_id, actor_id, is_admin=True
        )
        return f"Отметил «{task.task_name}» как выполненную."

    # --- Shared helpers ---------------------------------------------------

    async def _committed_hours(self) -> dict[UUID, int]:
        """Sum committed allocation hours per person across all committed plans."""
        from planner.app.add_project import deserialize_allocations

        used: dict[UUID, int] = defaultdict(int)
        for payload in await self._repo.list_committed_plans():
            for alloc in deserialize_allocations(payload):
                used[alloc.person_id] += alloc.hours
        return dict(used)


_EXECUTORS = {
    "get_team_load": ToolBox._get_team_load,
    "find_assignees": ToolBox._find_assignees,
    "list_people": ToolBox._list_people,
    "list_projects": ToolBox._list_projects,
    "capture_task": ToolBox._capture_task,
    "plan_project": ToolBox._plan_project,
    "set_vacation": ToolBox._set_vacation,
    "replan": ToolBox._replan,
    "assign_task": ToolBox._assign_task,
    "mark_task_done": ToolBox._mark_task_done,
    # confirm_plan intentionally NOT exposed: committing is manager-gated
    # (inline ✅ button / typed «ок»), never the agent (see module docstring).
}
