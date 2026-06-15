"""
CLI: python -m seed.load_seed

Loads seed data into the database. Idempotent — uses upsert on unique keys.
"""

import asyncio
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from planner.infra.db.base import create_engine, create_session_factory
from planner.infra.db.models import (
    Person,
    PersonRole,
    Role,
    RoleSkill,
    Skill,
    Template,
    TemplateDependency,
    TemplateTask,
    TemplateTaskAssignee,
)

SEED_DIR = Path(__file__).parent


async def load_team(session: AsyncSession) -> dict[str, object]:
    """Load people from team.yaml. Returns {name: Person} map.

    Idempotent: inserts only if name not already present.
    """
    raw = yaml.safe_load((SEED_DIR / "team.yaml").read_text(encoding="utf-8"))

    result = await session.execute(select(Person))
    existing: dict[str, Person] = {p.name: p for p in result.scalars().all()}

    for entry in raw["people"]:
        name = entry["name"]
        if name in existing:
            continue
        person = Person(
            name=name,
            role_label=entry.get("role_label"),
            capacity_h=entry.get("capacity_h", 8),
            is_admin=entry.get("is_admin", False),
            is_active=entry.get("is_active", True),
            is_external=entry.get("is_external", False),
        )
        session.add(person)
        existing[name] = person

    await session.flush()
    return existing


async def load_template(
    session: AsyncSession,
    code: str,
    name: str,
    tasks_yaml: Path,
    people_map: dict[str, object],
) -> None:
    """Load a template and its tasks from YAML. Idempotent by template code."""
    raw = yaml.safe_load(tasks_yaml.read_text(encoding="utf-8"))

    # Upsert template row
    result = await session.execute(select(Template).where(Template.code == code))
    template: Template | None = result.scalar_one_or_none()
    if template is None:
        template = Template(code=code, name=name)
        session.add(template)
        await session.flush()
    else:
        template.name = name

    # Drop existing child rows so re-run is clean
    existing_task_ids = (
        await session.execute(
            select(TemplateTask.id).where(TemplateTask.template_id == template.id)
        )
    ).scalars().all()

    if existing_task_ids:
        await session.execute(
            delete(TemplateDependency).where(
                TemplateDependency.template_task_id.in_(existing_task_ids)
            )
        )
        await session.execute(
            delete(TemplateTaskAssignee).where(
                TemplateTaskAssignee.template_task_id.in_(existing_task_ids)
            )
        )
        await session.execute(
            delete(TemplateTask).where(TemplateTask.template_id == template.id)
        )

    # Insert tasks
    ord_to_task: dict[int, TemplateTask] = {}
    for entry in raw["tasks"]:
        task = TemplateTask(
            template_id=template.id,
            ord=entry["ord"],
            name=entry["name"],
            duration_hours=entry["duration_hours"],
            is_splittable=entry.get("is_splittable", False),
            allow_two_assignees=entry.get("allow_two_assignees", False),
            optional_in_lite=entry.get("optional_in_lite", False),
        )
        session.add(task)
        ord_to_task[entry["ord"]] = task

    await session.flush()

    # Insert assignees and dependencies
    for entry in raw["tasks"]:
        task = ord_to_task[entry["ord"]]

        for asgn in entry.get("assignees", []):
            person = people_map.get(asgn["name"])
            if person is None:
                raise ValueError(f"Unknown assignee '{asgn['name']}' in {tasks_yaml.name}")
            session.add(
                TemplateTaskAssignee(
                    template_task_id=task.id,
                    person_id=person.id,
                    strictness=asgn["strictness"],
                )
            )

        for dep_ord in entry.get("depends_on", []):
            dep_task = ord_to_task.get(dep_ord)
            if dep_task is None:
                raise ValueError(
                    f"Task ord={entry['ord']} references unknown dep ord={dep_ord}"
                )
            session.add(
                TemplateDependency(
                    template_task_id=task.id,
                    depends_on_id=dep_task.id,
                    link_type="FS",
                )
            )

    await session.flush()


async def load_capability(
    session: AsyncSession, people_map: dict[str, object]
) -> None:
    """Load roles + their standard skills from capability.yaml and link people.

    A person's capability = union of the skills of the roles they hold. People are
    matched to roles by their ``role_label``. Idempotent: skills/roles upserted by
    name, join tables rebuilt.
    """
    raw = yaml.safe_load((SEED_DIR / "capability.yaml").read_text(encoding="utf-8"))

    # Existing skills/roles by name (dedup skills across roles).
    skills_by_name: dict[str, Skill] = {
        s.name: s for s in (await session.execute(select(Skill))).scalars().all()
    }
    roles_by_name: dict[str, Role] = {
        r.name: r for r in (await session.execute(select(Role))).scalars().all()
    }

    # Idempotent re-run: load existing join rows and skip duplicate inserts.
    # Never globally delete — that would wipe links the seed does not manage.
    existing_role_skills = {
        (rs.role_id, rs.skill_id)
        for rs in (await session.execute(select(RoleSkill))).scalars().all()
    }
    existing_person_roles = {
        (pr.person_id, pr.role_id)
        for pr in (await session.execute(select(PersonRole))).scalars().all()
    }

    for entry in raw["roles"]:
        role = roles_by_name.get(entry["name"])
        if role is None:
            role = Role(name=entry["name"], description=entry.get("description"))
            session.add(role)
            roles_by_name[entry["name"]] = role
        else:
            role.description = entry.get("description")
        await session.flush()

        for sk in entry.get("skills", []):
            skill = skills_by_name.get(sk["name"])
            if skill is None:
                skill = Skill(name=sk["name"], description=sk.get("description"))
                session.add(skill)
                skills_by_name[sk["name"]] = skill
                await session.flush()
            if (role.id, skill.id) not in existing_role_skills:
                session.add(RoleSkill(role_id=role.id, skill_id=skill.id))
                existing_role_skills.add((role.id, skill.id))

    # Link people to roles by role_label.
    for person in people_map.values():
        label = getattr(person, "role_label", None)
        if not label:
            continue
        role = roles_by_name.get(label)
        if role is not None and (person.id, role.id) not in existing_person_roles:
            session.add(PersonRole(person_id=person.id, role_id=role.id))
            existing_person_roles.add((person.id, role.id))

    await session.flush()


async def main() -> None:
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://planner:planner@localhost:5432/planner",
    )
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)

    async with session_factory() as session, session.begin():
        people_map = await load_team(session)
        await load_template(
            session, "standard", "Стандартный шаблон",
            SEED_DIR / "tasks_standard.yaml", people_map,
        )
        await load_template(
            session, "lite", "Лайт шаблон",
            SEED_DIR / "tasks_lite.yaml", people_map,
        )
        await load_capability(session, people_map)

    await engine.dispose()
    print("Seed loaded successfully.")


if __name__ == "__main__":
    asyncio.run(main())
