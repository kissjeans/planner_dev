"""tasks.time_start — time of day for timed tasks (meetings).

«Встреча в 10:15» lost its time entirely (work chat, 2026-07-08): the bot
plans by days, so the hour now lives on the task itself for display and the
Notion mirror. Solver stays day-granular (hourly scheduling deferred to v2).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-08 00:00:01.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("time_start", sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "time_start")
