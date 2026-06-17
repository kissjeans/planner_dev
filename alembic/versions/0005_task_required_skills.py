"""tasks.required_skills JSONB (NL draft enrichment, spec section 3 step 2).

Cluster F: the captured task draft carries LLM-inferred required skills so the
bot can surface assignee suggestions. Nullable-free with an empty-list default
so existing rows backfill to [].

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-17 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "required_skills",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "required_skills")
