"""ExplainPlanUseCase (spec sections 6.3 + 8, flow step 13).

Builds the deterministic Russian plan summary, then optionally enriches it with
the LLM. Any LLM failure degrades to the deterministic text so the bot always
answers (spec section 15). The LLM dependency is optional and injected.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

import structlog

from planner.bot.replies.plan_explainer import NameMap, explain_plan
from planner.domain.models import PlanResult

log = structlog.get_logger(__name__)


class LLMExplainerPort(Protocol):
    async def explain_plan(self, plan_summary: str) -> str: ...


class ExplainPlanUseCase:
    def __init__(self, llm: LLMExplainerPort | None = None) -> None:
        self._llm = llm

    async def execute(
        self,
        plan: PlanResult,
        task_names: NameMap,
        person_names: NameMap,
        *,
        deadline: date | None = None,
        earliest_end: date | None = None,
    ) -> str:
        summary = explain_plan(
            plan,
            task_names,
            person_names,
            deadline=deadline,
            earliest_end=earliest_end,
        )
        if self._llm is None:
            return summary
        try:
            enriched = await self._llm.explain_plan(summary)
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the bot
            log.warning("llm_explain_failed", error=str(exc))
            return summary
        return enriched.strip() or summary
