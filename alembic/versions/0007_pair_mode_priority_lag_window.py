"""C2 model extensions: pair_mode, executor priority, dependency lag, window.

Adds the fields the solver needs for the real presales template (plan C2):

- ``pair_mode`` on template_tasks/tasks (none|optional|required), backfilled
  from the deprecated ``allow_two_assignees`` flag (True -> 'optional').
- ``priority`` on template_task_assignees (0 = highest, chosen first).
- ``lag_working_days`` on template_dependencies/dependencies (e.g. FS + 5 days).
- ``duration_is_window`` on tasks (external resource window; template_tasks
  already had this column from the initial schema).

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-19 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # pair_mode on both template_tasks and tasks
    for table in ("template_tasks", "tasks"):
        op.add_column(
            table,
            sa.Column(
                "pair_mode",
                sa.String(length=8),
                nullable=False,
                server_default="none",
            ),
        )
        # Backfill from the deprecated bool flag.
        op.execute(
            f"UPDATE {table} SET pair_mode = 'optional' WHERE allow_two_assignees"
        )
        op.create_check_constraint(
            f"ck_pair_mode_{'template' if table == 'template_tasks' else 'task'}",
            table,
            "pair_mode IN ('none','optional','required')",
        )

    # duration_is_window on tasks (template_tasks already has it)
    op.add_column(
        "tasks",
        sa.Column(
            "duration_is_window",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # executor priority on template_task_assignees
    op.add_column(
        "template_task_assignees",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )

    # working-day lag on both dependency tables
    for table in ("template_dependencies", "dependencies"):
        op.add_column(
            table,
            sa.Column(
                "lag_working_days",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    for table in ("template_dependencies", "dependencies"):
        op.drop_column(table, "lag_working_days")
    op.drop_column("template_task_assignees", "priority")
    op.drop_column("tasks", "duration_is_window")
    for table in ("template_tasks", "tasks"):
        op.drop_constraint(
            f"ck_pair_mode_{'template' if table == 'template_tasks' else 'task'}",
            table,
            type_="check",
        )
        op.drop_column(table, "pair_mode")
