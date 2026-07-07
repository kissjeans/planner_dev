# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`planner` (dir `planner_dev`) — a Telegram bot that auto-schedules a presales team's
tasks across people and days (respecting capacity, dependencies, deadlines), with a
FastAPI+htmx admin board for hard edits. Single-process, single-tenant, one Postgres
writer. Full design lives in [docs/SPEC.md](docs/SPEC.md); deferred-to-v2 decisions in
[docs/architecture.md](docs/architecture.md).

Stack: Python 3.12 · uv · aiogram 3 · FastAPI · SQLAlchemy 2 async + asyncpg · Postgres 16 ·
Redis 7 (FSM storage) · NetworkX (greedy solver) · Anthropic Claude (Sonnet agent / Haiku
intent) · faster-whisper (voice STT) · APScheduler · matplotlib/seaborn (PNG renders).

## Commands

```bash
make dev          # run bot + web admin (:8000) + scheduler in ONE process
make test         # uv run pytest tests -v
make cov          # pytest with coverage (target 80%+)
make lint         # ruff check src tests  +  mypy src/planner/domain --strict
make migrate      # uv run alembic upgrade head
make seed         # uv run python -m seed.load  (loads seed/*.yaml)

uv sync --extra dev                              # install incl. test/lint deps
uv run pytest tests/unit/domain/solver/test_greedy_c2.py            # one file
uv run pytest tests/unit/app/test_add_project.py::test_name -v      # one test
RUN_LLM_EVAL=1 uv run pytest -m live             # opt-in live-LLM eval tests
```

`mypy --strict` is enforced **only on `src/planner/domain`** (the pure core); the rest is
linted by ruff but not strictly typed. ruff rules: `E,F,I,UP,B,SIM`, line-length 100.

## Infra & local gotcha

`docker compose up -d` starts Postgres + Redis. **Compose remaps host ports** to avoid
clashing with any system Postgres/Redis: Postgres `5433→5432`, Redis `6380→6379`, app
`8000`. When running the app on the host (`make dev`) against compose infra, `.env` must
point at the remapped ports — `DATABASE_URL=...@localhost:5433/planner`,
`REDIS_URL=redis://localhost:6380/0` — even though `.env.example` shows the in-container
5432/6379. The Docker `app` service (and `docker-entrypoint.sh`, which runs `alembic
upgrade head` then `python -m planner.main`) uses the in-network ports instead.

Integration/e2e tests need Docker: they spin up a real Postgres via `testcontainers`
(`tests/conftest.py`). Without Docker those tests skip; pure-unit tests still run.

## Architecture

Four layers, dependencies point **inward** — `domain` is pure (no IO) and knows nothing of
the outer layers:

```
bot/ (aiogram)   web/ (FastAPI + htmx + Jinja2)
        \              /
         app/  (use-cases: AddProject, CaptureTask, ConfirmPlan, SetVacation, ...)
            |
        domain/  (solver, calendar rules, models, capability)  ← pure, no IO
            |
        infra/  (db, llm, stt, calendar, notion, scheduler)
```

External integrations sit behind **ports** (Protocols) so adapters are swappable and tests
inject fakes:
- `RepoPort` (`app/ports.py`) → `SqlAlchemyRepo` (`infra/db/repo.py`) — the single DB writer.
- `IntentParserPort` → `ClaudeIntentParser` (Haiku) or `BasicIntentParser` (regex).
- `SolverPort` → `GreedySolver` (NetworkX DAG, topo-sort + earliest-fit; OR-Tools deferred).
- `TaskSinkPort` / `ProjectSinkPort` → Notion adapters (`infra/notion/`) or `Null*` no-ops.
- `WorkingCalendar` → live `isdayoff.ru` snapshot, falling back to `SnapshotCalendar`.
- `STTPort` → faster-whisper for Telegram voice notes.

### Keyless degrade (important invariant)

Every external dependency degrades instead of crashing when unconfigured/unreachable:
no `ANTHROPIC_API_KEY` → regex `BasicIntentParser`; no Notion creds → `NullTaskSink`;
isdayoff.ru down → offline `SnapshotCalendar`. The bot stays functional with zero API spend.
`main.py` logs which mode each subsystem started in.

### Agent vs. classifier

When a key is set and `agent_enabled` is true, messages go through **`PlannerAgent`**
(`infra/llm/agent.py`) — a Claude **Sonnet** tool-use loop (`MAX_ITERS=10`, temp 0) that
reads/reasons over the DB and acts via thin tool wrappers in `infra/llm/tools.py`, each of
which delegates to an existing `app/` use-case (no business logic is reimplemented in
tools). On any Anthropic error it degrades to the regex parser. Plain classification
(`ClaudeIntentParser`) can stay on Haiku; the orchestration loop needs Sonnet.

### Plan lifecycle & write-gate

Plans are versioned: `plan_project` only **proposes** a `PlanVersion` (`status=proposed`);
the agent has no confirm tool — a human presses the inline ✅ button (or types «ок») which
runs `ConfirmPlanUseCase`; any chat participant may confirm (R8, commit 9ca5b61). Write
tools (`capture_task`, `plan_project`, `set_vacation`, `replan`, `assign_task`,
`mark_task_done`) require `actor.is_admin` (admin set from `ADMIN_IDS`); reads are open to
everyone. The only concurrency guard is the compare-and-set status transition
(`transition_plan_status`, `infra/db/repo.py:126-144`): a losing concurrent confirm matches
zero rows and is rejected. A per-`project_id` Postgres advisory lock covering bot+web
writes is a known follow-up — it does **not** exist yet.

### Single-process entrypoint

`planner.main` builds the repo, bot dispatcher, solver (with calendar), FastAPI app, and
APScheduler in one asyncio loop, then `asyncio.gather(dp.start_polling, server.serve)`.
Scheduler jobs: daily team-load PNG to `TEAM_CHAT_ID`, and calendar-snapshot refresh.
`ensure_secure_config` **refuses to start** when `DEBUG=false` and `JWT_SECRET` is still
the insecure default (it would let an attacker forge admin sessions).

## Tests

`tests/` split by layer: `unit/` (domain solver/calendar/intent + use-cases, no IO),
`integration/` (db + calendar/LLM via vcrpy on a real Postgres), `e2e/` (bot flows + web
admin via mock updates), `eval/` (live-LLM quality, opt-in). Migrations live in
`alembic/versions/`; seed data is YAML in `seed/`.

## Branches

`main` is the default/PR base. `pravky_dev` is the active dev branch; `deploy` tracks
what's deployed. Remote: `github.com/kissjeans/planner_dev`.

# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.