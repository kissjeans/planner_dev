"""Unit tests for ExplainPlanUseCase (spec sections 6.3 + 8 + 15)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from planner.app.explain_plan import ExplainPlanUseCase
from planner.domain.models import Assignment, DayAllocation, PlanResult


def _plan():
    t, p = uuid4(), uuid4()
    a = Assignment(
        task_id=t,
        person_id=p,
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 8),
        allocations=(DayAllocation(p, date(2026, 6, 8), 8),),
    )
    return PlanResult(assignments=(a,), end_date=date(2026, 6, 8)), {t: "Бриф"}, {p: "Андрей"}


class _OkLLM:
    async def explain_plan(self, plan_summary: str) -> str:
        return "Кратко: всё ок."


class _BrokenLLM:
    async def explain_plan(self, plan_summary: str) -> str:
        raise RuntimeError("API down")


class _EmptyLLM:
    async def explain_plan(self, plan_summary: str) -> str:
        return "   "


@pytest.mark.asyncio
async def test_no_llm_returns_deterministic_summary():
    plan, tasks, people = _plan()
    out = await ExplainPlanUseCase(None).execute(plan, tasks, people)
    assert "Бриф → Андрей" in out


@pytest.mark.asyncio
async def test_llm_enriches_summary():
    plan, tasks, people = _plan()
    out = await ExplainPlanUseCase(_OkLLM()).execute(plan, tasks, people)
    assert out == "Кратко: всё ок."


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_deterministic():
    plan, tasks, people = _plan()
    out = await ExplainPlanUseCase(_BrokenLLM()).execute(plan, tasks, people)
    assert "Бриф → Андрей" in out


@pytest.mark.asyncio
async def test_blank_llm_output_falls_back():
    plan, tasks, people = _plan()
    out = await ExplainPlanUseCase(_EmptyLLM()).execute(plan, tasks, people)
    assert "Бриф → Андрей" in out
