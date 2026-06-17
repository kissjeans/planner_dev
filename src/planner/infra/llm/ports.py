"""Intent parser port and the context passed to it (spec section 6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from planner.domain.intent import Intent


@dataclass(frozen=True)
class ChatContext:
    """Context the parser uses to resolve names and relative dates."""

    today: date
    aliases: dict[str, str] = field(default_factory=dict)  # "лёху" -> "Лёша"
    known_people: tuple[str, ...] = ()
    known_projects: tuple[str, ...] = ()
    recent_messages: tuple[str, ...] = ()  # oldest→newest, for reference resolution


class IntentParserPort(Protocol):
    async def parse(self, text: str, ctx: ChatContext) -> Intent: ...
