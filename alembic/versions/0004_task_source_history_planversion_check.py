"""Task.source provenance, task_history groundwork, plan_versions.status CHECK.

Cluster D data-model groundwork (spec section 4, Ф4 задел):
- tasks.source ('bot_formed' | 'template') with a CHECK constraint.
- task_history table (no use case yet — future history-based matching).
- plan_versions.status restricted to ('proposed','committed').

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-16 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # --- tasks.source provenance ---
    op.add_column(
        "tasks",
        sa.Column("source", sa.Text(), nullable=False, server_default="bot_formed"),
    )
    op.create_check_constraint(
        "ck_task_source", "tasks", "source IN ('bot_formed','template')"
    )

    # --- task_history (groundwork only — no use case wires this yet) ---
    op.create_table(
        "task_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=False,
        ),
        sa.Column("task_name", sa.Text(), nullable=False),
        sa.Column("project_title", sa.Text(), nullable=True),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # --- plan_versions.status CHECK ---
    op.create_check_constraint(
        "ck_plan_version_status",
        "plan_versions",
        "status IN ('proposed','committed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_plan_version_status", "plan_versions", type_="check")
    op.drop_table("task_history")
    op.drop_constraint("ck_task_source", "tasks", type_="check")
    op.drop_column("tasks", "source")
