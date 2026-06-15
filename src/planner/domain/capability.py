"""Capability matching: suggest assignees by required skills + current load.

Pure domain logic (no IO, no ORM). The bot uses this as a *soft hint* on the
assignment step (spec section 5): a task needs certain skills, people whose
roles cover those skills are surfaced, ranked by coverage then by who is freer.
The author can always override — this never blocks an assignment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID


def _norm(skill: str) -> str:
    """Case-insensitive, whitespace-trimmed skill key for matching."""
    return skill.strip().casefold()


@dataclass(frozen=True)
class Candidate:
    """A person considered for a task, with their capability skill set.

    ``skills`` is the union of the skills of the person's roles (spec 4).
    ``load_hours`` is currently committed hours over the horizon (lower = freer).
    """

    person_id: UUID
    name: str
    skills: frozenset[str]
    is_external: bool = False
    load_hours: int = 0


@dataclass(frozen=True)
class AssigneeSuggestion:
    """A ranked suggestion for who could take a task."""

    person_id: UUID
    name: str
    coverage: float  # fraction of required skills the person has, [0.0, 1.0]
    covered_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    load_hours: int


def suggest_assignees(
    required_skills: Iterable[str],
    candidates: Iterable[Candidate],
    *,
    include_external: bool = False,
    limit: int | None = None,
) -> tuple[AssigneeSuggestion, ...]:
    """Rank candidates for a task by skill coverage, then by who is freer.

    Coverage is the share of ``required_skills`` a candidate's skills cover
    (1.0 when nothing is required — then ranking is purely by load). Matching is
    case-insensitive. External people (outsource pool, spec 2) are excluded
    unless ``include_external`` is set. Ties break by lower load, then name.
    """
    required = list(dict.fromkeys(s.strip() for s in required_skills if s.strip()))
    # Dedupe numerator and denominator on the SAME (normalized) basis so coverage
    # stays in [0, 1] even when the query carries case/whitespace-variant dupes.
    label_by_key: dict[str, str] = {}
    for s in required:
        label_by_key.setdefault(_norm(s), s)
    required_keys = list(label_by_key)

    suggestions: list[AssigneeSuggestion] = []
    for c in candidates:
        if c.is_external and not include_external:
            continue
        have = {_norm(s) for s in c.skills}
        covered_keys = [k for k in required_keys if k in have]
        missing_keys = [k for k in required_keys if k not in have]
        coverage = len(covered_keys) / len(required_keys) if required_keys else 1.0
        covered = tuple(label_by_key[k] for k in covered_keys)
        missing = tuple(label_by_key[k] for k in missing_keys)
        suggestions.append(
            AssigneeSuggestion(
                person_id=c.person_id,
                name=c.name,
                coverage=coverage,
                covered_skills=covered,
                missing_skills=missing,
                load_hours=c.load_hours,
            )
        )

    suggestions.sort(key=lambda s: (-s.coverage, s.load_hours, s.name))
    return tuple(suggestions if limit is None else suggestions[:limit])
