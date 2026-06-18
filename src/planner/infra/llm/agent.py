"""PlannerAgent — Claude tool-use loop (agentic planner Task 2).

Replaces the rigid single-intent classifier: the agent reads the message, reasons
over the DB via read tools, then acts via write tools (all thin wrappers over the
existing use-cases in :mod:`planner.infra.llm.tools`). The deterministic solver
stays the math — the agent only orchestrates and explains.

Loop contract (spec architecture pseudocode):
- ``MAX_ITERS=6`` tool rounds, ``temperature=0``, ``max_tokens=1024``.
- First user message = a context block (today / roster / projects / recent chat)
  + a ``---`` separator + the raw message.
- Each ``tool_use`` block is dispatched through :meth:`ToolBox.execute` (which
  itself never raises), and the results are fed back as ``tool_result`` blocks.
- ``proposed_pv_id`` is taken from ``toolbox.last_proposed_pv_id`` after the loop
  so the bot can attach the ✅/✏️ confirm buttons.
- On ANY anthropic exception → degrade to the regex :class:`BasicIntentParser`
  (describe text) so the bot still answers (spec section 15).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import structlog

if TYPE_CHECKING:
    from anthropic.types import MessageParam, ToolParam

from planner.infra.llm.basic import BasicIntentParser
from planner.infra.llm.ports import ChatContext
from planner.infra.llm.prompts import AGENT_SYSTEM_PROMPT
from planner.infra.llm.tools import TOOL_SCHEMAS, ToolBox

log = structlog.get_logger(__name__)

# Sonnet for the agentic loop: multi-step tool-use chains (read→reason→write,
# decompose, clarify) overwhelm Haiku, which stalls into the iteration cap and
# makes spurious duplicate captures. Classification (ClaudeIntentParser) can stay
# on Haiku; orchestration needs the stronger model.
_MODEL = "claude-sonnet-4-6"
_TIMEOUT_S = 45.0  # tool loops take several round-trips; allow more than classify
_MAX_RETRIES = 3
_MAX_ITERS = 10
_MAX_TOKENS = 1024
_TEMPERATURE = 0
_CAP_MESSAGE = "Не успел обработать — переформулируй короче."


@dataclass(frozen=True)
class AgentReply:
    """The agent's natural-language reply plus an optional proposed plan id."""

    text: str
    proposed_pv_id: UUID | None = None
    # Notion links of tasks captured this turn; the bot appends them because the
    # model drops links when it paraphrases tool output.
    notion_urls: tuple[str, ...] = ()
    # Partial task args when capture needs a missing key field; the bot renders
    # the clarify buttons instead of showing the model's text.
    clarify: dict[str, Any] | None = None
    # Deterministic capture confirmations; when present the bot shows these
    # verbatim instead of the model's narration (clean layout, one-task merges).
    captured_replies: tuple[str, ...] = ()


def _build_context_block(ctx: ChatContext) -> str:
    """Embed DB knowledge + chat continuity the agent reasons over."""
    people = ", ".join(ctx.known_people) or "—"
    projects = ", ".join(ctx.known_projects) or "—"
    recent = "\n".join(ctx.recent_messages) or "—"
    return (
        f"today={ctx.today.isoformat()}\n"
        f"команда: {people}\n"
        f"проекты: {projects}\n"
        f"недавние сообщения:\n{recent}"
    )


def _final_text(resp: Any) -> str:
    """Concatenate text blocks of a final (non-tool_use) response."""
    blocks = getattr(resp, "content", []) or []
    text = "".join(
        getattr(b, "text", "")
        for b in blocks
        if getattr(b, "type", "") == "text"
    )
    return text.strip() or _CAP_MESSAGE


class PlannerAgent:
    """Runs the Anthropic tool-use loop with a regex fallback safety net."""

    def __init__(self, api_key: str, fallback: BasicIntentParser | None = None) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(
            api_key=api_key, timeout=_TIMEOUT_S, max_retries=_MAX_RETRIES
        )
        self._fallback = fallback or BasicIntentParser()

    async def run(self, text: str, ctx: ChatContext, toolbox: ToolBox) -> AgentReply:
        first_user = f"{_build_context_block(ctx)}\n---\n{text}"
        messages: list[dict[str, Any]] = [{"role": "user", "content": first_user}]
        try:
            return await self._loop(messages, toolbox)
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the bot
            log.warning("agent_failed", error=str(exc))
            return AgentReply(text=self._fallback_text(text, ctx))

    async def _loop(self, messages: list[dict[str, Any]], toolbox: ToolBox) -> AgentReply:
        for _ in range(_MAX_ITERS):
            resp = await self._client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                temperature=_TEMPERATURE,
                system=AGENT_SYSTEM_PROMPT,
                tools=cast("list[ToolParam]", TOOL_SCHEMAS),
                messages=cast("list[MessageParam]", messages),
            )
            if resp.stop_reason != "tool_use":
                return AgentReply(
                    text=_final_text(resp),
                    proposed_pv_id=toolbox.last_proposed_pv_id,
                    notion_urls=tuple(toolbox.captured_notion_urls),
                    clarify=toolbox.pending_capture,
                    captured_replies=tuple(toolbox.captured_replies),
                )
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    args = block.input if isinstance(block.input, dict) else {}
                    out = await toolbox.execute(block.name, args)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": out,
                        }
                    )
            messages.append({"role": "user", "content": results})
        return AgentReply(
            text=_CAP_MESSAGE,
            proposed_pv_id=toolbox.last_proposed_pv_id,
            notion_urls=tuple(toolbox.captured_notion_urls),
            clarify=toolbox.pending_capture,
            captured_replies=tuple(toolbox.captured_replies),
        )

    def _fallback_text(self, text: str, ctx: ChatContext) -> str:
        """Describe the single intent the regex parser extracts (degraded mode)."""
        intent = self._fallback.parse_sync(text, ctx)
        return _describe_intent(intent)


def _describe_intent(intent: Any) -> str:
    """Short RU acknowledgement so the bot still answers in degraded mode."""
    kind = getattr(intent, "kind", "")
    if kind == "clarify":
        return getattr(intent, "question", "Не понял — переформулируй.")
    if kind == "load":
        return "Показываю загрузку команды."
    if kind == "capture_task":
        return f"Записал задачу: {getattr(intent, 'task_title', '').strip()}".rstrip(": ")
    if kind == "add_project":
        return f"Планирую проект «{getattr(intent, 'title', '')}»."
    if kind == "what_if":
        return "Моделирую сценарий (что-если)."
    if kind == "vacation":
        return f"Оформляю отпуск {getattr(intent, 'person_name', '')}.".rstrip()
    if kind == "confirm":
        return "Подтверждаю последний предложенный план."
    if kind == "assign":
        return "Переназначаю задачу."
    return "Принято."
