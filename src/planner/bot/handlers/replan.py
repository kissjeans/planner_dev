"""/replan handler (spec section 8: «запустить пересчёт»).

Re-runs the solver over the committed projects with the *current* day-overrides
(vacations entered since the last commit, etc.) and reports a refreshed load /
overload summary. READ-ONLY by design: it never persists a plan version and
never mutates committed plans — the manager re-commits explicitly via /task
edits if they want the new schedule to stick (spec: no silent overwrite).
"""

from __future__ import annotations

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
            if hours == 0:
                # Window / external resource (e.g. the design pool): no team
                # capacity is consumed, so it must not show up as an overload.
                continue
            tasks.append(
                Task(
                    id=tid,
                    name=name_map.get(tid, "task"),
                    duration_hours=hours,
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
    overloads: tuple[Any, ...],
    person_names: dict[UUID, str],
    assignments: tuple[Any, ...] = (),
    task_project: dict[UUID, str] | None = None,
) -> str:
    """Render the re-solve result: per overloaded day show who, the date and the
    projects that person is busy on that day; or an all-clear line."""
    if not overloads:
        return (
            "🔄 Пересчитал план по текущим данным.\n"
            "✅ Всё помещается — перегрузок нет.\n\n"
            "Это предпросмотр, план не менялся."
        )
    task_project = task_project or {}
    lines = [
        "🔄 Пересчитал план по текущим данным.",
        "",
        "⚠️ Перегрузки — в эти дни у человека больше работы, чем влезает в день:",
    ]
    # Sort by person name, then date, so the list reads top-to-bottom calmly.
    ordered = sorted(
        overloads,
        key=lambda r: (person_names.get(r.person_id, "—"), r.day or date.min),
    )
    for r in ordered:
        who = person_names.get(r.person_id, "—") if r.person_id is not None else "—"
        when = r.day.strftime("%d.%m.%Y") if r.day else "—"
        projects = sorted(
            {
                task_project.get(a.task_id, "проект")
                for a in assignments
                for al in a.allocations
                if al.person_id == r.person_id and al.day == r.day
            }
        )
        proj = ", ".join(projects) if projects else "—"
        lines.append(f"• {who} — {when}, проекты: {proj}")
    lines.append("")
    lines.append(
        "Это предпросмотр — план НЕ изменён. Чтобы применить новое "
        "расписание, внеси правки через /task."
    )
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
    task_project = await repo.get_task_project_map()
    return _format_summary(
        result.overloads(), person_names, result.assignments, task_project
    )


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
