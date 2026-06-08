"""SuggestAssigneesUseCase (spec section 5).

Ties the capability source (who has which skills) to current load (committed
hours), then asks the pure domain ranker for the best candidates. The result is
a *hint* shown on the assignment step — the author can always override.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from uuid import UUID

from planner.app.add_project import deserialize_allocations
from planner.app.ports import RepoPort
from planner.domain.capability import (
    AssigneeSuggestion,
    Candidate,
    suggest_assignees,
)

DEFAULT_LIMIT = 5


class SuggestAssigneesUseCase:
    def __init__(self, repo: RepoPort) -> None:
        self._repo = repo

    async def execute(
        self,
        required_skills: Iterable[str],
        *,
        include_external: bool = False,
        limit: int | None = DEFAULT_LIMIT,
    ) -> tuple[AssigneeSuggestion, ...]:
        caps = await self._repo.get_person_capabilities()
        load = await self._committed_load()
        candidates = [
            Candidate(
                person_id=c.person_id,
                name=c.name,
                skills=c.skills,
                is_external=c.is_external,
                load_hours=load.get(c.person_id, 0),
            )
            for c in caps
        ]
        return suggest_assignees(
            required_skills,
            candidates,
            include_external=include_external,
            limit=limit,
        )

    async def _committed_load(self) -> dict[UUID, int]:
        """Sum committed allocation hours per person across all committed plans."""
        load: dict[UUID, int] = defaultdict(int)
        for payload in await self._repo.list_committed_plans():
            for alloc in deserialize_allocations(payload):
                load[alloc.person_id] += alloc.hours
        return dict(load)
