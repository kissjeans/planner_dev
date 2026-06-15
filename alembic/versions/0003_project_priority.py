"""Add projects.priority (admin board: project priority column).

Revision ID: 0003
Revises: 25365964fe62
Create Date: 2026-06-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic
revision: str = "0003"
down_revision: str | None = "25365964fe62"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("priority", sa.Text(), nullable=False, server_default="medium"),
    )


def downgrade() -> None:
    op.drop_column("projects", "priority")
