# Plan 025: re-seeding does not wipe the whole capability graph

> **Executor instructions**: Follow step by step; run every verification before
> moving on. On a "STOP condition", stop and report. Update this plan's row in
> `plans/README.md` when done.
>
> **Drift check (run first)**: working tree was dirty at authoring time. Open
> `seed/load_seed.py` and confirm the quoted lines match before editing. On a
> mismatch, STOP.

## Status

- **Priority**: P3
- **Effort**: S–M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `442c9a4`, 2026-06-15

## Why this matters

`seed/load_seed.py` advertises itself as idempotent ("Idempotent — uses upsert on
unique keys", file docstring). `load_team` and `load_template` honor that. But
`load_capability` opens by **unconditionally deleting every row** in `RoleSkill`
and `PersonRole`:

```python
# load_seed.py:175-177
# Rebuild join tables for a clean re-run.
await session.execute(delete(RoleSkill))
await session.execute(delete(PersonRole))
```

So a re-run wipes the entire capability graph — including any role/skill links
created outside the seed — and only re-creates `PersonRole` for people whose
`role_label` matches a seeded role (load_seed.py:198-205). Anyone whose
`role_label` is null or not in `capability.yaml` loses their role assignment on
every re-run. The recent capability-based `/suggest` feature reads this graph, so
a re-seed silently resets the data it depends on. "Idempotent" should mean
"stable on re-run", not "reset to seed".

## Current state

```python
# load_seed.py:156-207 (load_capability), key lines
roles_by_name = {r.name: r for r in (await session.execute(select(Role))).scalars().all()}
skills_by_name = {s.name: s for s in (await session.execute(select(Skill))).scalars().all()}

await session.execute(delete(RoleSkill))     # global wipe
await session.execute(delete(PersonRole))    # global wipe

for entry in raw["roles"]:
    role = roles_by_name.get(entry["name"]) or Role(...)   # upserted by name
    ...
    for sk in entry.get("skills", []):
        skill = skills_by_name.get(sk["name"]) or Skill(...)
        session.add(RoleSkill(role_id=role.id, skill_id=skill.id))   # no dedup guard

for person in people_map.values():
    label = getattr(person, "role_label", None)
    if not label:
        continue
    role = roles_by_name.get(label)
    if role is not None:
        session.add(PersonRole(person_id=person.id, role_id=role.id))  # no dedup guard
```

Models imported at the top of the file: `Person, PersonRole, Role, RoleSkill,
Skill, Template, TemplateDependency, TemplateTask, TemplateTaskAssignee`. The
whole run executes inside one transaction (`async with ... session.begin()` in
`main`).

`load_template` already demonstrates the intended scoped-rebuild pattern: it
deletes child rows only for the template being loaded (load_seed.py:84-104), not
globally. Follow that shape.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| YAML tests | `uv run pytest tests/unit/test_seed_yaml.py` | all pass |
| Integration | `uv run pytest tests/integration` | all pass (needs Docker Postgres; see STOP) |
| Lint    | `ruff check .` | exit 0 |

## Scope

**In scope:**
- `seed/load_seed.py` (the `load_capability` function only)
- A new or extended seed-loader test (see Test plan)

**Out of scope:**
- `load_team` and `load_template` — already idempotent.
- `capability.yaml` content and the ORM models.
- The `/suggest` use-case and domain logic.

## Git workflow

- Branch: `advisor/025-seed-capability-idempotency`
- Conventional commits (e.g. `fix(seed): scope capability rebuild to seeded roles/people`).
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Scope the join-table rebuild to seeded entities

Replace the two global deletes with a rebuild scoped to exactly the roles and
people the seed manages, mirroring `load_template`'s scoped-delete pattern. Two
acceptable approaches — pick the simpler for this schema:

- **Scoped delete + insert**: collect the seeded role ids (the roles named in
  `capability.yaml`) and the seeded person ids (the people in `people_map` with a
  matching `role_label`); delete only `RoleSkill` for those role ids and
  `PersonRole` for those person ids, then re-insert.
- **Upsert / existence-guard**: before `session.add(RoleSkill(...))` /
  `PersonRole(...)`, check the link doesn't already exist (query or in-memory set
  of existing `(role_id, skill_id)` / `(person_id, role_id)` pairs) and skip
  duplicates.

Either way: a re-run must leave a previously-seeded graph **unchanged**, and must
not touch role/skill links for roles or people the seed does not manage.

**Verify**: `ruff check .` → exit 0. (DB behavior is verified in Step 2.)

### Step 2: Add an idempotency test

Add an integration test (model after the existing async-session integration
tests under `tests/integration/`) that:
1. runs `load_team` + `load_capability` twice over the same session/DB and asserts
   `RoleSkill` and `PersonRole` row counts are **identical** after the second run;
2. asserts that a manually-inserted extra `PersonRole` (a role link the seed does
   not manage) **survives** a re-run.

If standing up a Postgres session in the test suite is impractical in this
environment, write the test against the same async-session fixture the existing
integration tests use, and mark it to run there.

**Verify**: `uv run pytest tests/integration` → all pass (or, if Docker is
unavailable, see STOP conditions).

## Test plan

- New idempotency/integration test as above.
- Pattern reference: existing `tests/integration/` async-session tests
  (`test_repo_full.py` uses the real repo against Postgres) and the scoped-delete
  shape in `load_template`.
- Verification: `uv run pytest tests/integration` → all pass.

## Done criteria

ALL must hold:

- [ ] `grep -n "delete(RoleSkill)\|delete(PersonRole)" seed/load_seed.py` shows no **unconditional** global delete (either scoped, or replaced by an existence-guarded insert).
- [ ] A test asserts re-running the loader leaves capability row counts stable and preserves non-seeded links.
- [ ] `uv run pytest tests/integration` → exit 0 (or STOP reported if Docker unavailable).
- [ ] `ruff check .` → exit 0.
- [ ] No files outside the in-scope list modified.
- [ ] `plans/README.md` status row updated.

## STOP conditions

- The integration suite requires a Postgres container that is not available in
  this environment (`docker` not running) — write the test, confirm it imports
  and is collected, mark it appropriately, and report that it needs Docker to
  execute. Do not weaken the test to a no-op.
- Scoping the delete reveals the schema has no clean way to identify "seeded"
  vs "manual" links (e.g. no stable role-id set) — report and propose the
  existence-guard approach instead.

## Maintenance notes

- If a UI to edit roles/skills is ever added, this scoping becomes load-bearing
  (today the seed is effectively the only writer, so impact is currently
  limited — but the `/suggest` feature already reads the graph).
- The `RoleSkill`/`PersonRole` inserts also lack a uniqueness guard; if
  `capability.yaml` lists a skill twice under one role, duplicate links result.
  The existence-guard approach in Step 1 fixes that too — prefer it if unsure.
- Reviewer: confirm a double-run leaves counts stable (the core property).
