"""Unit tests for AdminBoardUseCase — Schedule/Calendar/Load from tasks."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from planner.app.admin_board import AdminBoardUseCase
from planner.app.ports import PersonRecord, TaskMeta

_A = uuid4()  # Андрей
_B = uuid4()  # Борис
_T1, _T2, _T3 = uuid4(), uuid4(), uuid4()
_START = date(2026, 6, 8)  # Monday


def _task(tid, name, project, priority, status, start, end, hours, aid, aname, dl):
    return TaskMeta(
        task_id=tid, task_name=name, project_title=project, priority=priority,
        status=status, start_date=start, end_date=end, duration_hours=hours,
        assignee_id=aid, assignee_name=aname, deadline=dl,
    )


def _build(days=4):
    people = [PersonRecord(_A, "Андрей", capacity_h=8),
              PersonRecord(_B, "Борис", capacity_h=8)]
    tasks = [
        # 2-day task, 24h → 12h/day → 3 slots/day → overload (cap 2)
        _task(_T1, "Бриф", "МТС", "high", "not_done",
              date(2026, 6, 8), date(2026, 6, 9), 24, _A, "Андрей", date(2026, 6, 10)),
        # single day, 8h → 2 slots
        _task(_T2, "Дизайн", "Билайн", "medium", "not_done",
              None, date(2026, 6, 8), 8, _B, "Борис", date(2026, 6, 9)),
        # unassigned, only deadline → schedule only
        _task(_T3, "Звонок", "Мегафон", "medium", "not_done",
              None, None, 8, None, None, date(2026, 6, 11)),
    ]
    return AdminBoardUseCase().build(tasks=tasks, people=people, start=_START, days=days)


def test_load_slots_and_overload():
    b = _build()
    a = next(r for r in b.load_rows if r.name == "Андрей")
    assert a.capacity_slots == 2
    assert a.slots == (3, 3, 0, 0)
    assert a.overloaded == (True, True, False, False)
    assert a.free == (0, 0, 2, 2)


def test_load_totals():
    b = _build()
    assert b.totals == (5, 3, 0, 0)  # А3+Б2, А3, 0, 0


def test_load_pct():
    b = _build()
    a = next(r for r in b.load_rows if r.name == "Андрей")
    assert a.pct == 75  # 6 used / 8 available


def test_schedule_sorted_by_deadline_includes_unassigned():
    b = _build()
    order = [r.task_name for r in b.schedule]
    assert order == ["Дизайн", "Бриф", "Звонок"]  # dl 09 < 10 < 11
    звонок = next(r for r in b.schedule if r.task_name == "Звонок")
    assert звонок.assignee_name == "—"  # unassigned still listed
    assert звонок.project_title == "Мегафон"


def test_calendar_cells_show_tasks_per_person_per_day():
    b = _build()
    andrey = next(r for r in b.calendar if r.person_name == "Андрей")
    assert andrey.cells == ("Бриф", "Бриф", "", "")
    boris = next(r for r in b.calendar if r.person_name == "Борис")
    assert boris.cells == ("Дизайн", "", "", "")


def test_unassigned_task_absent_from_calendar_and_load():
    b = _build()
    # Звонок has no assignee → no calendar cell, no load contribution day idx3
    assert all("Звонок" not in c for row in b.calendar for c in row.cells)
    assert b.totals[3] == 0


def test_task_span_clamped_to_window():
    people = [PersonRecord(_A, "Андрей", capacity_h=8)]
    # span 06-08..06-20 but window is only 4 days → later days dropped
    tasks = [_task(_T1, "Долгая", "МТС", "high", "not_done",
                   date(2026, 6, 8), date(2026, 6, 20), 8, _A, "Андрей",
                   date(2026, 6, 20))]
    b = AdminBoardUseCase().build(tasks=tasks, people=people, start=_START, days=4)
    # only 4 in-window days carry load; out-of-window days skipped
    a = b.load_rows[0]
    assert len(a.slots) == 4
    assert a.slots[0] >= 1  # first day inside window has load


def test_schedule_slots_total_per_task():
    b = _build()
    brief = next(r for r in b.schedule if r.task_name == "Бриф")
    assert brief.slots == 6  # 24h → 6 slots total
    assert brief.task_id == str(_T1)
    assert brief.assignee_id == str(_A)
