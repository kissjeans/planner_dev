"""Regex fallback intent parser (spec section 15 risk mitigation).

Deterministic, offline, no API key. Used when Claude is unavailable, and as a
fast-path / test double. Recognises the common command shapes; anything it
cannot classify becomes a :class:`ClarifyIntent` so the bot asks again.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from planner.domain.intent import (
    AddProjectIntent,
    ClarifyIntent,
    ConfirmIntent,
    Intent,
    LoadIntent,
    VacationIntent,
    WhatIfIntent,
)
from planner.infra.llm.ports import ChatContext

_RU_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_QUOTE = re.compile(r"[\"«»'„“”]([^\"«»'„“”]+)[\"«»'„“”]")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DM = re.compile(r"\b(\d{1,2})\s+([а-яё]+)", re.IGNORECASE)
_DDMM = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b")
_RANGE = re.compile(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})\s+([а-яё]+)", re.IGNORECASE)


def _parse_date(text: str, today: date) -> date | None:
    m = _ISO.search(text)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = _DDMM.search(text)
    if m:
        year = int(m[3]) if m[3] else today.year
        return date(year, int(m[2]), int(m[1]))
    m = _DM.search(text)
    if m:
        month = _month_num(m[2])
        if month:
            return date(today.year, month, int(m[1]))
    return None


def _month_num(word: str) -> int | None:
    word = word.lower()
    for stem, num in _RU_MONTHS.items():
        if word.startswith(stem):
            return num
    return None


def _resolve_person(text: str, ctx: ChatContext) -> str | None:
    low = text.lower()
    for alias, canonical in ctx.aliases.items():
        if alias.lower() in low:
            return canonical
    for name in ctx.known_people:
        if name.lower() in low:
            return name
    return None


class BasicIntentParser:
    """Synchronous logic exposed through an async ``parse`` for the port."""

    async def parse(self, text: str, ctx: ChatContext) -> Intent:
        return self.parse_sync(text, ctx)

    def parse_sync(self, text: str, ctx: ChatContext) -> Intent:
        low = text.lower().lstrip("/").strip()

        _load_kw = ("load", "загруз", "нагруз", "загузк", "нагузк")
        if any(kw in low for kw in _load_kw) or "/load" in text:
            return LoadIntent(person_name=_resolve_person(text, ctx))

        # "какие задачи / статус / что сейчас" → show team load
        _task_query_kw = ("задач", "что сейчас", "что идёт", "статус", "текущ")
        if any(kw in low for kw in _task_query_kw):
            return LoadIntent(person_name=_resolve_person(text, ctx))

        if "отпуск" in low or low.startswith("vacation"):
            return self._vacation(text, ctx)

        if "что-если" in low or "что если" in low or low.startswith("whatif"):
            return self._what_if(text, ctx)

        if low in {"ок", "ok", "да", "yes", "confirm", "подтверждаю"}:
            return ConfirmIntent()

        if any(k in low for k in ("проект", "project", "новый проект", "add")):
            title_m = _QUOTE.search(text)
            if title_m:
                template: Literal["standard", "lite"] = (
                    "lite" if "lite" in low or "лайт" in low else "standard"
                )
                return AddProjectIntent(
                    title=title_m[1].strip(),
                    template_code=template,
                    deadline=_parse_date(text, ctx.today),
                )

        return ClarifyIntent(
            question=(
                "Не понял. Умею:\n"
                "/load — загрузка команды\n"
                "/task <текст> — новый проект\n"
                "/whatif — что-если\n"
                "/confirm — подтвердить план\n"
                "или напиши «загрузка», «отпуск», «что-если»"
            )
        )

    def _vacation(self, text: str, ctx: ChatContext) -> Intent:
        person = _resolve_person(text, ctx)
        rng = _RANGE.search(text)
        if person and rng:
            month = _month_num(rng[3])
            if month:
                d_from = date(ctx.today.year, month, int(rng[1]))
                d_to = date(ctx.today.year, month, int(rng[2]))
                return VacationIntent(
                    person_name=person, day_from=d_from, day_to=d_to
                )
        return ClarifyIntent(question="Укажи имя и даты отпуска.")

    def _what_if(self, text: str, ctx: ChatContext) -> Intent:
        low = text.lower()
        op: Literal["shift_deadline", "add_person", "switch_to_lite", "drop_project"]
        if "lite" in low or "лайт" in low:
            op = "switch_to_lite"
        elif "человек" in low or "+1" in low:
            op = "add_person"
        elif "убери" in low or "drop" in low or "удали проект" in low:
            op = "drop_project"
        else:
            op = "shift_deadline"
        title_m = _QUOTE.search(text)
        return WhatIfIntent(
            operation=op,
            project_title=title_m[1].strip() if title_m else None,
            new_deadline=_parse_date(text, ctx.today) if op == "shift_deadline" else None,
            person_name=_resolve_person(text, ctx) if op == "add_person" else None,
        )
