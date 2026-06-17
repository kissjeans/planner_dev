"""plan_versions.status CHECK allows 'superseded' (cluster 1 fix).

The edit-loop retires a replaced proposal via
``transition_plan_status(old_pv_id, 'proposed', 'superseded')``, but migration
0004's ``ck_plan_version_status`` only permitted ('proposed','committed') — so
on real Postgres every plan edit raised IntegrityError. Widen the constraint to
include 'superseded' so constraint and code agree.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-17 00:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_plan_version_status", "plan_versions", type_="check")
    op.create_check_constraint(
        "ck_plan_version_status",
        "plan_versions",
        "status IN ('proposed','committed','superseded')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_plan_version_status", "plan_versions", type_="check")
    op.create_check_constraint(
        "ck_plan_version_status",
        "plan_versions",
        "status IN ('proposed','committed')",
    )
