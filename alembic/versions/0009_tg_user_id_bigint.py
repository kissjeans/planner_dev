"""people.tg_user_id Integer -> BigInteger.

Modern Telegram user ids exceed int32 (e.g. 6137672320): every message from
such an account crashed actor resolution with «value out of int32 range»
(prod, 2026-07-07). Telegram ids are documented to fit in a signed 64-bit int.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-07 00:00:01.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.alter_column(
        "people",
        "tg_user_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "people",
        "tg_user_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
