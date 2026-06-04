"""Claude Haiku intent parser via instructor (spec section 6.2).

Falls back to :class:`BasicIntentParser` on any API error so the bot keeps
working when Claude is unreachable (spec section 15).
"""

from __future__ import annotations

import structlog

from planner.domain.intent import Intent
from planner.infra.llm.basic import BasicIntentParser
from planner.infra.llm.ports import ChatContext
from planner.infra.llm.prompts import (
    EXPLAIN_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    build_user_message,
)

log = structlog.get_logger(__name__)

_MODEL = "claude-haiku-4-5-20251001"


class ClaudeIntentParser:
    """Implements :class:`IntentParserPort` with a regex safety net."""

    def __init__(self, api_key: str, fallback: BasicIntentParser | None = None) -> None:
        import instructor
        from anthropic import AsyncAnthropic

        self._client = instructor.from_anthropic(AsyncAnthropic(api_key=api_key))
        self._raw = AsyncAnthropic(api_key=api_key)
        self._fallback = fallback or BasicIntentParser()

    async def parse(self, text: str, ctx: ChatContext) -> Intent:
        try:
            return await self._client.messages.create(
                model=_MODEL,
                response_model=Intent,
                max_tokens=400,
                max_retries=2,
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_message(text, ctx)},
                ],
            )
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the bot
            log.warning("claude_intent_failed", error=str(exc))
            return await self._fallback.parse(text, ctx)

    async def explain_plan(self, plan_summary: str) -> str:
        resp = await self._raw.messages.create(
            model=_MODEL,
            max_tokens=400,
            messages=[
                {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                {"role": "user", "content": plan_summary},
            ],
        )
        return "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
