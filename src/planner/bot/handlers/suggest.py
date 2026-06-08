"""/suggest handler (spec section 5) — capability-based assignee hint.

``/suggest <скилл>, <скилл>, …`` surfaces who on the team could take a task that
needs those skills, ranked by skill coverage then by who is freer right now.
Read-only hint: it never assigns anyone, the author decides (spec 5, 9).
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from planner.app.ports import RepoPort
from planner.app.suggest_assignees import SuggestAssigneesUseCase
from planner.domain.capability import AssigneeSuggestion

router = Router(name="suggest")

_USAGE = "Укажи нужные скиллы: /suggest Копирайтинг, Редактура"


def _parse_skills(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def _format_line(s: AssigneeSuggestion) -> str:
    pct = round(s.coverage * 100)
    line = f"• {s.name} — покрытие {pct}% (загрузка {s.load_hours}ч)"
    if s.missing_skills:
        line += f"\n  не хватает: {', '.join(s.missing_skills)}"
    return line


def format_suggestions(skills: list[str], suggestions: tuple[AssigneeSuggestion, ...]) -> str:
    """Human-readable suggestion list for the chat (spec 6: days/words, not timers)."""
    if not suggestions:
        return f"По скиллам ({', '.join(skills)}) подходящих людей не нашлось."
    header = f"Кто может ({', '.join(skills)}):"
    return header + "\n" + "\n".join(_format_line(s) for s in suggestions)


async def build_suggestion_text(repo: RepoPort, raw: str) -> str:
    """Orchestrate the suggestion query end to end (testable without aiogram)."""
    skills = _parse_skills(raw)
    if not skills:
        return _USAGE
    suggestions = await SuggestAssigneesUseCase(repo).execute(skills)
    return format_suggestions(skills, suggestions)


@router.message(Command("suggest"))
async def handle_suggest(message: Message, repo: RepoPort | None = None) -> None:
    raw = (message.text or "").partition(" ")[2]
    if repo is None:
        await message.answer("Подбор недоступен: репозиторий не подключён.")
        return
    await message.answer(await build_suggestion_text(repo, raw))
