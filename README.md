# planner_dev

Telegram bot that auto-schedules a presales team's tasks across people and days,
respecting capacity, dependencies and deadlines — with a FastAPI admin for
hard edits. One process, one Postgres writer, single-tenant.

See [the implementation spec](users-possstum-desktop-work-id-planer-p-woolly-ullman.md)
for the full design and the [acceptance scenarios](docs/acceptance.md).

## Architecture

Four layers, dependencies point inward:

```
bot/ (aiogram)  web/ (FastAPI+htmx)
        \            /
         app/ (use-cases)
            |
       domain/ (solver, calendar)   <- pure, no IO
            |
       infra/ (db, llm, stt, calendar, scheduler)
```

External integrations sit behind ports (`SolverPort`, `IntentParserPort`,
`WorkingCalendar`, `RepoPort`, `STTPort`) so adapters are swappable.

## Stack

Python 3.12 · aiogram 3 · FastAPI · SQLAlchemy 2 (async) · Postgres 16 ·
Redis 7 · NetworkX (greedy solver) · Claude Haiku (intent) · Whisper (STT) ·
APScheduler · matplotlib · uv.

## Quick start

```bash
# 1. Infra
docker compose up -d            # postgres + redis

# 2. Deps
uv sync

# 3. Config
cp .env.example .env            # fill BOT_TOKEN, ANTHROPIC_API_KEY, ADMIN_IDS, ...

# 4. DB + seed
uv run alembic upgrade head
uv run python -m seed.load

# 5. Run (bot + admin on :8000 + scheduler)
uv run python -m planner.main
```

## Make targets

`make dev` · `make test` · `make cov` · `make seed` · `make migrate` ·
`make lint` · `make acceptance`

## Tests

```bash
uv run pytest tests --cov=src/planner --cov-report=term-missing
```

Levels: unit (domain solver, calendar, intent, use-cases — no IO), integration
(db, calendar/LLM via vcrpy on a real Postgres), e2e (bot flows + web admin).

## Status

Sprints 1–6 implemented: schema + seed, greedy solver, intent parsing + bot
baseline, use-cases + PNG render, FastAPI admin, observability + error boundary.
Deferred to v2 (spec §14): OR-Tools/PyJobShop, multi-tenant, production deploy.
