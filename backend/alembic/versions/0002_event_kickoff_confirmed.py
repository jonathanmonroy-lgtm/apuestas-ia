"""event.kickoff_confirmed: real kick-off time vs date-only placeholder

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14 18:00:00

Existing rows default to False (conservative: never claim a time we haven't confirmed);
the next fixtures sync sets the real value for every event.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("event", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kickoff_confirmed", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("event", schema=None) as batch_op:
        batch_op.drop_column("kickoff_confirmed")
