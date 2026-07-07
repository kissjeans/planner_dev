"""SetVacationUseCase (spec section 7.4 / 5).

Upserts day overrides for a person across a date range, then the bot triggers
a replan. Admin-gated and audited.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from planner.app.ports import RepoPort
from planner.domain.intent import VacationIntent

MAX_VACATION_DAYS = 60
MIN_CAPACITY_H = 0
MAX_CAPACITY_H = 12


class PersonNotFoundError(Exception):
    pass


class InvalidVacationError(ValueError):
    """Vacation input out of bounds (reversed/oversized range, bad capacity)."""


class SetVacationUseCase:
    def __init__(self, repo: RepoPort) -> None:
        self._repo = repo

    async def execute(
        self, intent: VacationIntent, actor_id: UUID | None, *, is_admin: bool
    ) -> int:
        if not is_admin:
            raise PermissionError("Только админ может оформлять отпуск.")

        if intent.day_from > intent.day_to:
            raise InvalidVacationError("Дата начала отпуска позже даты окончания.")
        range_days = (intent.day_to - intent.day_from).days + 1
        if range_days > MAX_VACATION_DAYS:
            raise InvalidVacationError(
                f"Слишком длинный период: {range_days} дней, максимум {MAX_VACATION_DAYS}."
            )
        if not MIN_CAPACITY_H <= intent.capacity_h <= MAX_CAPACITY_H:
            raise InvalidVacationError(
                f"Ёмкость должна быть от {MIN_CAPACITY_H} до {MAX_CAPACITY_H} часов."
            )

        person = await self._repo.get_person_by_name(intent.person_name)
        if person is None:
            raise PersonNotFoundError(intent.person_name)

        day = intent.day_from
        count = 0
        while day <= intent.day_to:
            await self._repo.upsert_day_override(
                person.id, day, intent.capacity_h, "vacation"
            )
            count += 1
            day += timedelta(days=1)

        await self._repo.add_audit(
            actor_id,
            "set_vacation",
            "day_override",
            person.id,
            {
                "from": intent.day_from.isoformat(),
                "to": intent.day_to.isoformat(),
                "capacity_h": intent.capacity_h,
            },
        )
        return count
