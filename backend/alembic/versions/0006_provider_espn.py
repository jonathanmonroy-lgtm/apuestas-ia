"""provider 'espn' (multi-country matches of the day, no key)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17 18:00:00

`provider` columns are VARCHAR + CHECK (no native enum, see app.models.enums.db_enum):
adding a value = recreating each CHECK constraint.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "external_ref",
    "ingestion_run",
    "provider_quota",
    "news_item",
    "unmatched_odd",
    "event_result",
    "odd_snapshot",
)
OLD = "('api_football', 'football_data', 'the_odds_api', 'newsapi')"
NEW = "('api_football', 'football_data', 'the_odds_api', 'newsapi', 'espn')"


def _swap(values: str) -> None:
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(op.f(f"ck_{table}_provider"), type_="check")
            batch_op.create_check_constraint(op.f(f"ck_{table}_provider"), f"source IN {values}")


def upgrade() -> None:
    _swap(NEW)


def downgrade() -> None:
    # The old schema cannot hold 'espn': its provider-tagged rows go (as 0005's downgrade
    # drops country_name). Source-agnostic rows (events, teams) stay, without ESPN refs.
    for table in reversed(TABLES):  # children (odd_snapshot -> ingestion_run) first
        op.execute(f"DELETE FROM {table} WHERE source = 'espn'")  # noqa: S608 - constants
    _swap(OLD)
