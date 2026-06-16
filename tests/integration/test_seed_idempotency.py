"""Integration test: seed capability loader is idempotent and never wipes the
graph globally (plan 025). Needs a real Postgres (testcontainers / Docker)."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select

from planner.infra.db.models import Person, PersonRole, Role, RoleSkill, Skill
from seed.load_seed import load_capability, load_team


async def _counts(session_factory) -> tuple[int, int]:
    async with session_factory() as s:
        rs = await s.scalar(select(func.count()).select_from(RoleSkill))
        pr = await s.scalar(select(func.count()).select_from(PersonRole))
        return int(rs or 0), int(pr or 0)


@pytest.mark.asyncio
async def test_seed_capability_idempotent_and_preserves_manual_links(db_session_factory):
    try:
        # First load.
        async with db_session_factory() as s, s.begin():
            people = await load_team(s)
            await load_capability(s, people)
        rs1, pr1 = await _counts(db_session_factory)
        assert rs1 > 0 and pr1 > 0

        # Insert a PersonRole the seed does NOT manage: a person linked to a role
        # they are not normally assigned to. The old global-wipe loader would
        # delete this on re-run; the fixed loader must preserve it.
        async with db_session_factory() as s, s.begin():
            person_id = await s.scalar(select(Person.id).limit(1))
            linked = set(
                (
                    await s.scalars(
                        select(PersonRole.role_id).where(
                            PersonRole.person_id == person_id
                        )
                    )
                ).all()
            )
            if linked:
                role_id = await s.scalar(
                    select(Role.id).where(Role.id.not_in(linked)).limit(1)
                )
            else:
                role_id = await s.scalar(select(Role.id).limit(1))
            assert role_id is not None
            s.add(PersonRole(person_id=person_id, role_id=role_id))
        _, pr_with_manual = await _counts(db_session_factory)
        assert pr_with_manual == pr1 + 1

        # Re-run the seed — counts stay stable and the manual link survives.
        async with db_session_factory() as s, s.begin():
            people = await load_team(s)
            await load_capability(s, people)
        rs2, pr2 = await _counts(db_session_factory)

        assert rs2 == rs1  # role_skills unchanged (no dup rows, no wipe)
        assert pr2 == pr_with_manual  # manual link preserved (no global wipe)
    finally:
        # Clean the shared session-scoped DB for the other integration tests.
        async with db_session_factory() as s, s.begin():
            await s.execute(delete(PersonRole))
            await s.execute(delete(RoleSkill))
            await s.execute(delete(Person))
            await s.execute(delete(Role))
            await s.execute(delete(Skill))
