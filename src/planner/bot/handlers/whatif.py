"""/whatif handler (spec section 8.1 / 7.2 / 14).

Reconstructs a PlanRequest from committed allocations, applies the what-if
operation, re-solves, and renders the diff. Falls back to intent echo when
no committed plan or solver is available.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from planner.app.ports import RepoPort
from planner.app.what_if import WhatIfUseCase
from planner.bot.replies.plan_explainer import explain_diff
from planner.domain.intent import WhatIfIntent
from planner.domain.models import PlanRequest, Task
from planner.domain.permissions import can_execute
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
    if not can_execute(intent.kind, actor.get("is_admin", False)):
        await message.answer("Только админ может править план.")
        return

    if repo is not None and solver is not None:
        base_req = await _base_request(repo, solver)
        if base_req is not None and base_req.tasks:
            diff = WhatIfUseCase(solver).execute(base_req, intent)
            summary = explain_diff(diff, {}, {})
            target = intent.project_title or "—"
            await message.answer(f"Что-если ({intent.operation}, проект {target}):\n{summary}")
            return

    target = intent.project_title or "—"
    await message.answer(f"Что-если: {intent.operation}, проект {target}.")
