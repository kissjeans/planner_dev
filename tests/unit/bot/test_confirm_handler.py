"""Unit tests for the confirm callback handler (Notion mirror warning)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from planner.app.confirm_plan import ConfirmResult
from planner.app.ports import PlanVersionRecord
from planner.bot.handlers import confirm


class _CbAnswers:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer(self, text: str = "", **kwargs: Any) -> None:
        self.calls.append(text)


def _callback(data: str) -> tuple[SimpleNamespace, _CbAnswers]:
    cb_answers = _CbAnswers()
    cb = SimpleNamespace(data=data, answer=cb_answers.answer)
    return cb, cb_answers


class _FakeConfirmUC:
    def __init__(self, result: ConfirmResult) -> None:
        self._result = result

    async def execute(self, plan_version_id: Any, actor: Any) -> ConfirmResult:
        return self._result


def _result(failed: int, total: int) -> ConfirmResult:
    plan = PlanVersionRecord(uuid4(), uuid4(), "committed", {})
    return ConfirmResult(plan=plan, mirror_total=total, mirror_failed=failed)


@pytest.mark.asyncio
async def test_confirm_clean_mirror_no_warning():
    cb, cb_answers = _callback(f"confirm:{uuid4()}")
    await confirm.handle_confirm(
        cb,  # type: ignore[arg-type]
        {"is_admin": True},
        confirm_uc=_FakeConfirmUC(_result(0, 3)),  # type: ignore[arg-type]
    )
    assert cb_answers.calls == ["✅ План зафиксирован."]


@pytest.mark.asyncio
async def test_confirm_partial_mirror_failure_warns():
    cb, cb_answers = _callback(f"confirm:{uuid4()}")
    await confirm.handle_confirm(
        cb,  # type: ignore[arg-type]
        {"is_admin": True},
        confirm_uc=_FakeConfirmUC(_result(2, 5)),  # type: ignore[arg-type]
    )
    reply = cb_answers.calls[0]
    assert "✅ План зафиксирован." in reply
    assert "⚠️ Notion: не удалось отразить 2 из 5 задач" in reply
