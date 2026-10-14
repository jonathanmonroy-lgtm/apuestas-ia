from alembic import command
from sqlalchemy import inspect

from tests.conftest import alembic_config

EXPECTED_TABLES = {
    "competition", "season", "participant", "external_ref",
    "event", "event_result", "odd_snapshot", "unmatched_odd",
    "news_item", "news_item_participant",
    "user_account", "spending_limit", "self_exclusion",
    "bet", "forecast", "forecast_outcome", "report",
    "ingestion_run", "provider_quota",
}  # fmt: skip


def test_upgrade_creates_all_tables(migrated_engine):
    tables = set(inspect(migrated_engine).get_table_names())
    assert EXPECTED_TABLES <= tables


def test_models_and_migrations_are_in_sync(migrated_engine, db_url):
    # Fails if someone changes a model without adding a migration.
    command.check(alembic_config(db_url))


def test_downgrade_then_upgrade_again(migrated_engine, db_url):
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    assert "event" not in inspect(migrated_engine).get_table_names()
    command.upgrade(cfg, "head")
    assert "event" in inspect(migrated_engine).get_table_names()
