"""Claude Haiku intent parser (spec section 6.2).

Asks Claude for a single intent as raw JSON and validates it with a Pydantic
``TypeAdapter`` over the discriminated :data:`Intent` union. Falls back to
:class:`BasicIntentParser` on any API or validation error so the bot keeps
working when Claude is unreachable (spec section 15).
"""

from __future__ import annotations

import structlog
from pydantic import TypeAdapter

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
_TIMEOUT_S = 10.0   # chat UX: past this, the regex fallback is better
# Transient 429/5xx/529 (Overloaded) storms usually clear in a couple seconds;
# the SDK retries these with backoff before we degrade to the regex parser.
_MAX_RETRIES = 3
_INTENT_ADAPTER: TypeAdapter[Intent] = TypeAdapter(Intent)

_TEMPERATURE = 0  # deterministic classification — same command, same intent

_JSON_INSTRUCTION = (
    "\n\nОтветь ТОЛЬКО одним JSON-объектом intent (с полем kind). "
    "Без markdown, без ```, без пояснений до или после."
)


def _strip_fences(text: str) -> str:
    """Drop ```json fences the model sometimes wraps JSON in."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        t = t.removeprefix("json").strip()
        if t.endswith("```"):
            t = t[: -3].strip()
    return t


def _extract_text(resp: object) -> str:
    blocks = getattr(resp, "content", [])
    return "".join(
        getattr(b, "text", "")
        for b in blocks
        if getattr(b, "type", "") == "text"
    )


class ClaudeIntentParser:
    """Implements :class:`IntentParserPort` with a regex safety net."""

    def __init__(self, api_key: str, fallback: BasicIntentParser | None = None) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(
            api_key=api_key, timeout=_TIMEOUT_S, max_retries=_MAX_RETRIES
        )
        self._fallback = fallback or BasicIntentParser()

    async def parse(self, text: str, ctx: ChatContext) -> Intent:
        try:
            resp = await self._client.messages.create(
                model=_MODEL,
                max_tokens=400,
                temperature=_TEMPERATURE,
                system=INTENT_SYSTEM_PROMPT + _JSON_INSTRUCTION,
                messages=[{"role": "user", "content": build_user_message(text, ctx)}],
            )
            raw = _strip_fences(_extract_text(resp))
            return _INTENT_ADAPTER.validate_json(raw)
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the bot
            log.warning("claude_intent_failed", error=str(exc))
            return await self._fallback.parse(text, ctx)

    async def explain_plan(self, plan_summary: str) -> str:
        try:
            resp = await self._client.messages.create(
                model=_MODEL,
                max_tokens=400,
                system=EXPLAIN_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": plan_summary}],
            )
            return _extract_text(resp) or plan_summary
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the bot
            log.warning("claude_explain_failed", error=str(exc))
            return plan_summary
