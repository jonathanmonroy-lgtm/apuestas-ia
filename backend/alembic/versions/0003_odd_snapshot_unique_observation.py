"""odd_snapshot: unique observation (idempotent odds ingestion)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14 19:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ["event_id", "source", "bookmaker", "market", "selection", "captured_at"]


def upgrade() -> None:
    with op.batch_alter_table("odd_snapshot", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_odd_snapshot_observation", COLUMNS)


def downgrade() -> None:
    with op.batch_alter_table("odd_snapshot", schema=None) as batch_op:
        batch_op.drop_constraint("uq_odd_snapshot_observation", type_="unique")
