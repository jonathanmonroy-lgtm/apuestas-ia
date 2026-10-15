"""competition.country_name (country menu; England/World have no ISO alpha-2 code)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("competition", schema=None) as batch_op:
        batch_op.add_column(sa.Column("country_name", sa.String(length=60), nullable=True))
    # LaLiga (football-data) was ingested before this column existed.
    op.execute("UPDATE competition SET country_name = 'Spain' WHERE country_code = 'ES'")


def downgrade() -> None:
    with op.batch_alter_table("competition", schema=None) as batch_op:
        batch_op.drop_column("country_name")
