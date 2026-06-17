"""/load handler (spec section 8.1 + 7.4).

Renders the 14-day team load heatmap from committed plans. Reconstructs per-day
allocations from committed ``plan_versions.payload`` (no re-solve needed). Falls
back to a text acknowledgement when the repo is not wired.
"""

from __future__ import annotations

from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from planner.app.add_project import deserialize_allocations
from planner.app.load_summary import DEFAULT_DAYS, LoadSummaryUseCase
from planner.app.ports import RepoPort
from planner.domain.models import DayAllocation
from planner.infra.llm.ports import ChatContext, IntentParserPort

router = Router(name="load")


async def build_load_image(
    repo: RepoPort,
    *,
    start: date,
    days: int = DEFAULT_DAYS,
    person_name: str | None = None,
) -> bytes | None:
    """Build the load heatmap PNG, or None when there is no team to render.

    When ``person_name`` names a known person the heatmap is narrowed to just
    them; an unknown name falls back to the whole team (spec 8.1 /load <имя>).
    """
    people = list(await repo.get_solver_people())
    if not people:
        return None

    if person_name:
        matched = [p for p in people if p.name.casefold() == person_name.casefold()]
        if matched:
            people = matched

    allocations: list[DayAllocation] = []
    for payload in await repo.list_committed_plans():
        allocations.extend(deserialize_allocations(payload))

    return LoadSummaryUseCase().execute(people, allocations, start, days)


@router.message(Command("load"))
async def handle_load(
    message: Message, parser: IntentParserPort, repo: RepoPort | None = None
) -> None:
    text = (message.text or "").partition(" ")[2].strip() or "load"
    intent = await parser.parse(text, ChatContext(today=date.today()))
    person_name = getattr(intent, "person_name", None)
    who = person_name or "вся команда"

    if repo is None:
        await message.answer(f"Загрузка ({who}): репозиторий не подключён.")
        return

    png = await build_load_image(repo, start=date.today(), person_name=person_name)
    if png is None:
        await message.answer("В команде нет активных людей — нечего показывать.")
        return

    await message.answer_photo(
        BufferedInputFile(png, filename="load.png"),
        caption=f"Загрузка ({who}) на {DEFAULT_DAYS} дней.",
    )
