"""Unit tests for the what-if diff explainer (spec section 14)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from planner.bot.replies.plan_explainer import explain_diff
from planner.domain.models import PlanDiff, RiskFlag


def test_no_changes():
    out = explain_diff(PlanDiff(), {}, {})
    assert "Сдвигов задач нет" in out


def test_moved_tasks_listed_by_name():
    t1, t2 = uuid4(), uuid4()
    diff = PlanDiff(moved_tasks=(t1, t2))
    out = explain_diff(diff, {t1: "Бриф", t2: "Дизайн"}, {})
    assert "Сдвинется задач: 2" in out
    assert "Бриф" in out and "Дизайн" in out


def test_new_overloads_reported():
    p = uuid4()
    diff = PlanDiff(
        new_overloads=(
            RiskFlag(kind="overload", message="10ч > 8ч", person_id=p, day=date(2026, 6, 9)),
        )
    )
    out = explain_diff(diff, {}, {p: "Айгуль"})
    assert "Новые перегрузы: 1" in out
    assert "Айгуль 09.06" in out


def test_removed_overloads_reported():
    p = uuid4()
    diff = PlanDiff(
        removed_overloads=(RiskFlag(kind="overload", message="ушёл", person_id=p),)
    )
    out = explain_diff(diff, {}, {p: "Лёша"})
    assert "Уйдут перегрузы: 1" in out
    assert "Лёша" in out
