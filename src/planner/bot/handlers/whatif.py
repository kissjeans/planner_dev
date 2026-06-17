"""/whatif handler (spec section 8.1 / 7.2 / 14).

Reconstructs a PlanRequest from committed allocations, applies the what-if
operation, re-solves, and renders the diff. Falls back to intent echo when
no committed plan or solver is available.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from planner.app.add_project import ProjectTemplate, instantiate_template
from planner.app.ports import RepoPort
from planner.app.what_if import WhatIfUseCase
from planner.bot.replies.plan_explainer import explain_diff
from planner.domain.intent import WhatIfIntent
from planner.domain.models import PlanRequest, Task
from planner.domain.solver.ports import SolverPort
from planner.infra.llm.ports import ChatContext, IntentParserPort

router = Router(name="whatif")


async def _base_request(repo: RepoPort, solver: SolverPort) -> PlanRequest | None:
    """Reconstruct a PlanRequest from committed plan allocations (spec 7.2 step 1)."""
    people = await repo.get_solver_people()
    if not people:
        return None
    payloads = await repo.list_committed_plans()
    name_map = await repo.get_task_name_map()
    tasks: list[Task] = []
    task_ids: set[UUID] = set()
    for payload in payloads:
        for a in payload.get("assignments", []):
            tid = UUID(a["task_id"])
            hours = sum(al["hours"] for al in a.get("allocations", []))
            tasks.append(
                Task(
                    id=tid,
                    name=name_map.get(tid, "task"),
                    duration_hours=max(hours, 1),
                    allowed_person_ids=(UUID(a["person_id"]),),
                )
            )
            task_ids.add(tid)
    # Real dependency edges among the committed tasks (plan 022): the greedy
    # solver must honour precedence, else base and modified plans are both wrong.
    deps = tuple(
        d
        for d in await repo.list_task_dependencies()
        if d.task_id in task_ids and d.depends_on_id in task_ids
    )
    return PlanRequest(
        people=people,
        tasks=tuple(tasks),
        dependencies=deps,
        horizon_start=date.today(),
    )


async def _lite_request(repo: RepoPort) -> PlanRequest | None:
    """Rebuild a PlanRequest from the LITE template (spec §6: switch_to_lite).

    Mirrors AddProjectUseCase's template instantiation: the lite template's task
    set is the real scope-reduced alternative. Returns None when no lite template
    can be mapped, so the caller can answer a friendly message instead of a no-op.
    The template's own ``allowed_person_ids`` need not match current people — the
    greedy solver falls back to the whole team, keeping the lite plan feasible.
    """
    template = await repo.get_project_template("lite")
    if not isinstance(template, ProjectTemplate) or not template.tasks:
        return None
    people = await repo.get_solver_people()
    if not people:
        return None
    tasks, deps = instantiate_template(template, uuid4())
    return PlanRequest(
        people=people,
        tasks=tasks,
        dependencies=deps,
        horizon_start=date.today(),
    )


async def _is_committed_project(repo: RepoPort, title: str | None) -> bool:
    """True when ``title`` names a project that has a committed plan.

    Guards drop_project: without a real, committed target the operation would
    drop the entire committed workload (apply_operation clears all tasks). Full
    per-project scoping is a larger change (follow-up); this guard prevents the
    destructive no-target case (spec 7.2 / Cluster 3).
    """
    name = (title or "").strip()
    if not name:
        return False
    project = await repo.get_project_by_title(name)
    if project is None:
        return False
    return await repo.get_committed_plan(project.id) is not None


@router.message(Command("whatif"))
async def handle_whatif(
    message: Message,
    parser: IntentParserPort,
    actor: dict[str, Any],
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
) -> None:
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Опиши сценарий: /whatif <текст>.")
        return

    intent = await parser.parse(text, ChatContext(today=date.today()))
    if not isinstance(intent, WhatIfIntent):
        await message.answer("Это не похоже на сценарий «что-если». Переформулируй.")
        return

    if repo is not None and solver is not None:
        base_req = await _base_request(repo, solver)
        if base_req is not None and base_req.tasks:
            target = intent.project_title or "—"
            if intent.operation == "switch_to_lite":
                await _answer_switch_to_lite(message, repo, solver, base_req, target)
                return
            if intent.operation == "drop_project" and not await _is_committed_project(
                repo, intent.project_title
            ):
                await message.answer(
                    "Укажи проект для удаления, например: "
                    "/whatif удали проект «Альфа»."
                )
                return
            diff = WhatIfUseCase(solver).execute(base_req, intent)
            summary = explain_diff(diff, {}, {})
            await message.answer(f"Что-если ({intent.operation}, проект {target}):\n{summary}")
            return

    target = intent.project_title or "—"
    await message.answer(f"Что-если: {intent.operation}, проект {target}.")


async def _answer_switch_to_lite(
    message: Message,
    repo: RepoPort,
    solver: SolverPort,
    base_req: PlanRequest,
    target: str,
) -> None:
    """Render the real scope reduction of switching the project to its lite template.

    Diffs the committed full plan against a freshly-solved lite plan (in memory,
    no DB write). When no lite template maps, answers a friendly message rather
    than silently no-op'ing (spec §6 / Cluster G).
    """
    lite_req = await _lite_request(repo)
    if lite_req is None:
        await message.answer("Не могу сопоставить lite-шаблон для этого проекта.")
        return
    base_plan = solver.plan(base_req)
    lite_plan = solver.plan(lite_req)
    diff = solver.diff(base_plan, lite_plan)
    summary = explain_diff(diff, {}, {})
    scope = (
        f"Объём сократится: {len(base_req.tasks)} → {len(lite_req.tasks)} задач(и)."
    )
    await message.answer(
        f"Что-если (switch_to_lite, проект {target}):\n{scope}\n{summary}"
    )
