"""SetVacationUseCase (spec section 7.4 / 5).

Upserts day overrides for a person across a date range, then the bot triggers
a replan. Admin-gated and audited.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from planner.app.ports import RepoPort
from planner.domain.intent import VacationIntent


class PersonNotFoundError(Exception):
    pass


class SetVacationUseCase:
    def __init__(self, repo: RepoPort) -> None:
        self._repo = repo

    async def execute(
        self, intent: VacationIntent, actor_id: UUID | None, *, is_admin: bool
    ) -> int:
        if not is_admin:
            raise PermissionError("Только админ может оформлять отпуск.")

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
