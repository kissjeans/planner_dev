#!/bin/sh
# Apply DB migrations, then start the bot + admin + scheduler.
set -e

uv run alembic upgrade head
exec uv run python -m planner.main
