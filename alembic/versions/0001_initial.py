"""Initial schema: all 11 domain tables.

Revision ID: 0001
Revises:
Create Date: 2026-06-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # --- people ---
    op.create_table(
        "people",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tg_user_id", sa.Integer(), nullable=True, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role_label", sa.Text(), nullable=True),
        sa.Column("capacity_h", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_external", sa.Boolean(), nullable=False, server_default="false"),
    )

    # --- templates ---
    op.create_table(
        "templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
    )

    # --- template_tasks ---
    op.create_table(
        "template_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("templates.id"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column(
            "duration_is_window",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "is_splittable", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "allow_two_assignees",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "optional_in_lite", sa.Boolean(), nullable=False, server_default="false"
        ),
    )

    # --- template_task_assignees ---
    op.create_table(
        "template_task_assignees",
        sa.Column(
            "template_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("template_tasks.id"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            primary_key=True,
        ),
        sa.Column("strictness", sa.String(1), nullable=False),
        sa.CheckConstraint("strictness IN ('A','B','C')", name="ck_strictness"),
    )

    # --- template_dependencies ---
    op.create_table(
        "template_dependencies",
        sa.Column(
            "template_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("template_tasks.id"),
            primary_key=True,
        ),
        sa.Column(
            "depends_on_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("template_tasks.id"),
            primary_key=True,
        ),
        sa.Column("link_type", sa.String(2), nullable=False),
        sa.CheckConstraint(
            "link_type IN ('FS','SS')", name="ck_link_type_template"
        ),
    )

    # --- projects ---
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("templates.id"),
            nullable=True,
        ),
        sa.Column("brief_return_date", sa.Date(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="planning"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
    )

    # --- tasks ---
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "template_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("template_tasks.id"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="not_done"),
        sa.Column(
            "is_preliminary", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "is_splittable", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "allow_two_assignees",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # --- assignments ---
    op.create_table(
        "assignments",
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            primary_key=True,
        ),
        sa.Column("hours", sa.Integer(), nullable=False),
    )

    # --- dependencies ---
    op.create_table(
        "dependencies",
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id"),
            primary_key=True,
        ),
        sa.Column(
            "depends_on_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id"),
            primary_key=True,
        ),
        sa.Column("link_type", sa.String(2), nullable=False),
        sa.CheckConstraint("link_type IN ('FS','SS')", name="ck_link_type_dep"),
    )

    # --- day_overrides ---
    op.create_table(
        "day_overrides",
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("capacity_h", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
    )

    # --- plan_versions ---
    op.create_table(
        "plan_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )

    # --- audit_log ---
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("plan_versions")
    op.drop_table("day_overrides")
    op.drop_table("dependencies")
    op.drop_table("assignments")
    op.drop_table("tasks")
    op.drop_table("projects")
    op.drop_table("template_dependencies")
    op.drop_table("template_task_assignees")
    op.drop_table("template_tasks")
    op.drop_table("templates")
    op.drop_table("people")
