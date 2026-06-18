"""Unit tests for the deterministic plan explainer (spec section 8)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from planner.bot.replies.plan_explainer import explain_plan
from planner.domain.models import Assignment, DayAllocation, PlanResult, RiskFlag


def _assignment(task_id, person_id, start, end):
    return Assignment(
        task_id=task_id,
        person_id=person_id,
        start_date=start,
        end_date=end,
        allocations=(DayAllocation(person_id, start, 8),),
    )


def test_empty_plan():
    out = explain_plan(PlanResult(assignments=()), {}, {})
    assert "пуст" in out


def test_lists_assignments_with_names_and_window():
    t, p = uuid4(), uuid4()
    plan = PlanResult(
        assignments=(_assignment(t, p, date(2026, 6, 8), date(2026, 6, 9)),),
        end_date=date(2026, 6, 9),
    )
    out = explain_plan(plan, {t: "Бриф"}, {p: "Андрей"})
    assert "Бриф → Андрей" in out
    assert "08.06–09.06" in out
    assert "Завершение: 09.06" in out


def test_single_day_window_collapses():
    t, p = uuid4(), uuid4()
    plan = PlanResult(assignments=(_assignment(t, p, date(2026, 6, 8), date(2026, 6, 8)),))
    out = explain_plan(plan, {t: "Бриф"}, {p: "Андрей"})
    assert "(08.06)" in out


def test_overloads_reported():
    t, p = uuid4(), uuid4()
    plan = PlanResult(
        assignments=(_assignment(t, p, date(2026, 6, 8), date(2026, 6, 8)),),
        risks=(
            RiskFlag(kind="overload", message="10ч > 8ч", person_id=p, day=date(2026, 6, 8)),
        ),
    )
    out = explain_plan(plan, {t: "Бриф"}, {p: "Андрей"})
    assert "Перегрузы: 1" in out
    assert "Андрей 08.06" in out


def test_deadline_reachable_verdict():
    t, p = uuid4(), uuid4()
    plan = PlanResult(
        assignments=(_assignment(t, p, date(2026, 6, 8), date(2026, 6, 8)),),
        end_date=date(2026, 6, 8),
    )
    out = explain_plan(plan, {t: "Бриф"}, {p: "Андрей"}, deadline=date(2026, 6, 20))
    assert "достижим" in out
    assert "недостижим" not in out


def test_explicit_deadline_missed_stands_without_levers():
    """Variant A: a hard manager deadline stands — warn (plan tight), no levers."""
    t, p = uuid4(), uuid4()
    plan = PlanResult(
        assignments=(_assignment(t, p, date(2026, 6, 25), date(2026, 6, 25)),),
        end_date=date(2026, 6, 25),
    )
    out = explain_plan(
        plan, {t: "Бриф"}, {p: "Андрей"},
        deadline=date(2026, 6, 20), earliest_end=date(2026, 7, 12),
    )
    assert "жёсткий" in out
    assert "недостижим" not in out
    assert "Рычаги" not in out
    assert "12.07" in out  # earliest folded into the warning, not a separate line


def test_backward_mode_earliest_end():
    out = explain_plan(PlanResult(assignments=()), {}, {}, earliest_end=date(2026, 7, 12))
    assert "Самая ранняя дата завершения: 12.07" in out


def test_backward_mode_missed_deadline_still_levers():
    """No explicit deadline given but plan overloads → levers remain (backward aid)."""
    t, p = uuid4(), uuid4()
    plan = PlanResult(
        assignments=(_assignment(t, p, date(2026, 6, 25), date(2026, 6, 25)),),
        risks=(
            RiskFlag(kind="overload", message="≈2 раб. дн.", person_id=p, day=date(2026, 6, 25)),
        ),
        end_date=date(2026, 6, 25),
    )
    out = explain_plan(plan, {t: "Бриф"}, {p: "Андрей"})  # no deadline
    assert "Рычаги" in out
    assert "lite" in out
    assert "whatif" in out.lower() or "/whatif" in out


def test_deadline_reachable_no_levers():
    t, p = uuid4(), uuid4()
    plan = PlanResult(
        assignments=(_assignment(t, p, date(2026, 6, 8), date(2026, 6, 8)),),
        end_date=date(2026, 6, 8),
    )
    out = explain_plan(plan, {t: "Бриф"}, {p: "Андрей"}, deadline=date(2026, 6, 20))
    assert "Рычаги" not in out


def test_overload_offers_levers():
    """Spec §6: an overload is a soft signal that must offer levers."""
    t, p = uuid4(), uuid4()
    plan = PlanResult(
        assignments=(_assignment(t, p, date(2026, 6, 8), date(2026, 6, 8)),),
        risks=(
            RiskFlag(kind="overload", message="≈2 раб. дн.", person_id=p, day=date(2026, 6, 8)),
        ),
    )
    out = explain_plan(plan, {t: "Бриф"}, {p: "Андрей"})  # no deadline
    assert "Рычаги" in out
    assert "lite" in out
    assert "/whatif" in out


def test_explicit_deadline_suppresses_levers_even_with_overload():
    """Variant A: a hard manager deadline stands — no lever choice, just a warning."""
    t, p = uuid4(), uuid4()
    plan = PlanResult(
        assignments=(_assignment(t, p, date(2026, 6, 25), date(2026, 6, 25)),),
        risks=(
            RiskFlag(kind="overload", message="≈2 раб. дн.", person_id=p, day=date(2026, 6, 25)),
        ),
        end_date=date(2026, 6, 25),
    )
    out = explain_plan(plan, {t: "Бриф"}, {p: "Андрей"}, deadline=date(2026, 6, 20))
    assert "Рычаги" not in out
    assert "жёсткий" in out
