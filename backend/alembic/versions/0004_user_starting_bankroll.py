"""user_account: starting bankroll for the bet tracker

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.types

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_account", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("starting_bankroll", app.db.types.DecimalType(14, 2), nullable=True)
        )
        batch_op.add_column(sa.Column("bankroll_currency", sa.String(length=3), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_account", schema=None) as batch_op:
        batch_op.drop_column("bankroll_currency")
        batch_op.drop_column("starting_bankroll")
