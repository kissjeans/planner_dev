# planner_dev

Telegram bot that auto-schedules a presales team's tasks across people and days,
respecting capacity, dependencies and deadlines — with a FastAPI+htmx admin board
for hard edits. One process, one Postgres writer, single-tenant.

See [the implementation spec](docs/SPEC.md) for the full design,
[docs/architecture.md](docs/architecture.md) for deferred-to-v2 decisions, and the
[acceptance scenarios](docs/acceptance.md).

## Architecture

Four layers, dependencies point inward:

```
bot/ (aiogram)  web/ (FastAPI+htmx)
        \            /
         app/ (use-cases)
            |
       domain/ (solver, calendar)   <- pure, no IO
            |
       infra/ (db, llm, stt, calendar, notion, scheduler)
```

External integrations sit behind ports (`RepoPort`, `SolverPort`, `IntentParserPort`,
`TaskSinkPort`/`ProjectSinkPort`, `WorkingCalendar`, `STTPort`) so adapters are
swappable — and every one degrades instead of crashing when unconfigured: no
`ANTHROPIC_API_KEY` → regex parser, no Notion creds → no-op sinks, isdayoff.ru
down → offline calendar snapshot. The bot stays functional with zero API spend.

## Stack

Python 3.12 · uv · aiogram 3 · FastAPI · SQLAlchemy 2 (async) · Postgres 16 ·
Redis 7 · NetworkX (greedy solver) · Anthropic Claude — Sonnet agent tool-loop
for orchestration, Haiku for plain intent classification · Notion mirror
(project master cards + task checkboxes) · faster-whisper (local voice STT,
no API key) · APScheduler · matplotlib.

## Quick start

```bash
# 1. Infra
docker compose up -d            # postgres + redis

# 2. Deps
uv sync --extra dev

# 3. Config
cp .env.example .env            # fill BOT_TOKEN, ANTHROPIC_API_KEY, ADMIN_IDS, ...
# NB: compose remaps host ports — when running on the host, point .env at
# localhost:5435 (postgres) and localhost:6380 (redis).

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
(db, calendar/LLM via vcrpy on a real Postgres via testcontainers), e2e (bot
flows + web admin). Without Docker the integration/e2e layers skip.

## Status

MVP feature-complete: task capture, project planning with propose→confirm
lifecycle, vacation reshuffle + replan, load heatmaps, daily team-load summary,
Notion mirror, voice input, web admin (plans / team / audit) behind Telegram
login. What-if simulation and the `/whatif`/`/suggest` commands were dropped
from the MVP (commits 404cd28, a86f0e1) — assignee suggestion survives as an
agent tool. Deferred to v2 (spec §14): OR-Tools/PyJobShop solver, multi-tenant,
production hardening.
