"""/replan handler (spec section 8: «запустить пересчёт»).

Re-runs the solver over the committed projects with the *current* day-overrides
(vacations entered since the last commit, etc.) and reports a refreshed load /
overload summary. READ-ONLY by design: it never persists a plan version and
never mutates committed plans — the manager re-commits explicitly via /task
edits if they want the new schedule to stick (spec: no silent overwrite).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any
from uuid import UUID

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from planner.app.ports import RepoPort
from planner.domain.models import DayOverride, PlanRequest, Task
from planner.domain.solver.ports import SolverPort

router = Router(name="replan")


async def _reconstruct_request(
    repo: RepoPort, today: date
) -> PlanRequest | None:
    """Rebuild the solver request from committed plans + current overrides.

    Returns None when there is nothing committed to re-plan.
    """
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
    if not tasks:
        return None
    deps = tuple(
        d
        for d in await repo.list_task_dependencies()
        if d.task_id in task_ids and d.depends_on_id in task_ids
    )
    overrides: tuple[DayOverride, ...] = tuple(await repo.list_day_overrides())
    return PlanRequest(
        people=tuple(people),
        tasks=tuple(tasks),
        dependencies=deps,
        horizon_start=today,
        day_overrides=overrides,
    )


def _format_summary(
    overloads: tuple[Any, ...], person_names: dict[UUID, str]
) -> str:
    """Render the re-solve result: overloads per person, or an all-clear line."""
    if not overloads:
        return "Пересчёт готов: перегрузок нет — план сходится."
    by_person: dict[UUID | None, int] = defaultdict(int)
    for r in overloads:
        by_person[r.person_id] += 1
    lines = ["Пересчёт готов. Возможные перегрузки:"]
    for pid, count in by_person.items():
        who = person_names.get(pid, "—") if pid is not None else "—"
        lines.append(f"• {who}: {count} дн.")
    lines.append("План не изменён — внеси правки через /task, чтобы зафиксировать.")
    return "\n".join(lines)


async def build_replan_summary(
    repo: RepoPort, solver: SolverPort, *, today: date
) -> str:
    """Re-solve committed work with current overrides; return a text summary."""
    request = await _reconstruct_request(repo, today)
    if request is None:
        return "Нечего пересчитывать — нет зафиксированных планов."
    result = solver.plan(request)
    person_names = {p.id: p.name for p in request.people}
    return _format_summary(result.overloads(), person_names)


@router.message(Command("replan"))
async def handle_replan(
    message: Message,
    actor: dict[str, Any],
    repo: RepoPort | None = None,
    solver: SolverPort | None = None,
) -> None:
    # /replan re-solves committed work — an admin-only action (spec section 8),
    # gated explicitly here since it is a command, not a parsed write intent.
    if not actor.get("is_admin", False):
        await message.answer("Только админ может запускать пересчёт.")
        return
    if repo is None or solver is None:
        await message.answer("Пересчёт недоступен: репозиторий не подключён.")
        return
    await message.answer(await build_replan_summary(repo, solver, today=date.today()))
