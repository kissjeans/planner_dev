"""Render a ``PlanResult`` as a short Russian summary (spec section 8, flow step 13).

Pure presentation logic — no IO, no LLM. The LLM-based ``explain_plan`` (spec
6.3) is an optional richer variant; this deterministic formatter is the always-on
fallback and keeps the bot useful when the LLM is unavailable (spec section 15).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from planner.domain.models import PlanDiff, PlanResult

NameMap = dict[UUID, str]


def _fmt_day(d: date) -> str:
    return d.strftime("%d.%m")


def _name(name_map: NameMap, key: UUID, fallback: str) -> str:
    return name_map.get(key, fallback)


def explain_plan(
    plan: PlanResult,
    task_names: NameMap,
    person_names: NameMap,
    *,
    deadline: date | None = None,
    earliest_end: date | None = None,
) -> str:
    """Build a multi-line plan summary: assignments, overloads, deadline verdict."""
    lines: list[str] = []

    if not plan.assignments:
        lines.append("План пуст: задач нет.")
    else:
        lines.append(f"План: {len(plan.assignments)} задач(и).")
        for a in plan.assignments:
            task = _name(task_names, a.task_id, "задача")
            who = _name(person_names, a.person_id, "—")
            window = (
                _fmt_day(a.start_date)
                if a.start_date == a.end_date
                else f"{_fmt_day(a.start_date)}–{_fmt_day(a.end_date)}"
            )
            lines.append(f"• {task} → {who} ({window})")

    overloads = plan.overloads()
    if overloads:
        lines.append(f"⚠ Перегрузы: {len(overloads)}.")
        for r in overloads:
            who = _name(person_names, r.person_id, "—") if r.person_id else "—"
            day = f" {_fmt_day(r.day)}" if r.day else ""
            lines.append(f"  – {who}{day}: {r.message}")

    missed = [r for r in plan.risks if r.kind == "deadline_missed"]
    if deadline is not None:
        if missed or (plan.end_date is not None and plan.end_date > deadline):
            lines.append(f"❌ Дедлайн {_fmt_day(deadline)} недостижим.")
        else:
            lines.append(f"✅ Дедлайн {_fmt_day(deadline)} достижим.")

    if earliest_end is not None:
        lines.append(f"Самая ранняя дата завершения: {_fmt_day(earliest_end)}.")
    elif plan.end_date is not None:
        lines.append(f"Завершение: {_fmt_day(plan.end_date)}.")

    return "\n".join(lines)


def explain_diff(
    diff: PlanDiff,
    task_names: NameMap,
    person_names: NameMap,
) -> str:
    """Render a what-if ``PlanDiff`` as a short Russian summary (spec 14)."""
    lines: list[str] = []

    moved = diff.moved_tasks
    if moved:
        names = ", ".join(_name(task_names, t, "задача") for t in moved)
        lines.append(f"Сдвинется задач: {len(moved)} ({names}).")
    else:
        lines.append("Сдвигов задач нет.")

    for label, risks in (
        ("Новые перегрузы", diff.new_overloads),
        ("Уйдут перегрузы", diff.removed_overloads),
    ):
        if risks:
            lines.append(f"{label}: {len(risks)}.")
            for r in risks:
                who = _name(person_names, r.person_id, "—") if r.person_id else "—"
                day = f" {_fmt_day(r.day)}" if r.day else ""
                lines.append(f"  – {who}{day}: {r.message}")

    return "\n".join(lines)
