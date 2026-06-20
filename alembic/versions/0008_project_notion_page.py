"""projects.notion_page_id — Notion master-card link (C3 / R6).

Stores the Notion page id of a project's master card so status sync
(done→checkbox) can target it later. Nullable: NULL when Notion is off.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-19 00:00:01.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("notion_page_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "notion_page_id")
