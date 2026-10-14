"""Fixtures/results sync: manual entry point; the worker (app.worker) calls `sync_fixtures`.

Usage (from backend/):
    uv run python -m app.jobs.sync_fixtures                 # every FIXTURES_COMPETITIONS
    uv run python -m app.jobs.sync_fixtures --competition PD --season 2026

Exit code is 0 only if every competition synced successfully, so cron/CI can alert on it.
"""

import argparse
import sys

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.models.enums import IngestionStatus, QuotaPeriod
from app.services.fixtures_provider import ProviderNotConfiguredError, get_fixtures_provider
from app.services.ingestion.fixtures_job import run_fixtures_ingestion
from app.services.quota import QuotaPolicy

log = get_logger(__name__)

EXIT_OK, EXIT_FAILED, EXIT_NOT_CONFIGURED = 0, 1, 2


def quota_policy(settings: Settings) -> QuotaPolicy:
    if settings.fixtures_provider == "api_football":
        return QuotaPolicy(QuotaPeriod.DAY, settings.api_football_daily_budget)
    return QuotaPolicy(QuotaPeriod.DAY, settings.football_data_daily_budget)


def sync_fixtures(
    settings: Settings,
    session_factory: sessionmaker[Session],
    competitions: list[str] | None = None,
    season: int | None = None,
) -> int:
    """Sync every competition. Returns an exit code; never raises for provider problems."""
    try:
        provider = get_fixtures_provider(settings)
    except ProviderNotConfiguredError as exc:
        log.error("fixtures_provider_not_configured", error=str(exc))
        return EXIT_NOT_CONFIGURED
    ok = True
    try:
        for code in competitions or settings.fixtures_competitions:
            outcome = run_fixtures_ingestion(
                session_factory, provider, code, policy=quota_policy(settings), season=season
            )
            ok = ok and outcome.status is IngestionStatus.SUCCESS
    finally:
        provider.close()
    return EXIT_OK if ok else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync fixtures and results from the provider")
    parser.add_argument("--competition", action="append", help="internal code, e.g. PD")
    parser.add_argument("--season", type=int, help="start year, e.g. 2026 for 2026-27")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    from app.db.session import SessionLocal  # engine built only when actually running

    return sync_fixtures(settings, SessionLocal, args.competition, args.season)


if __name__ == "__main__":
    sys.exit(main())
