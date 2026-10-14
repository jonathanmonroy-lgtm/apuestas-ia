"""Odds sync: manual entry point; the worker (app.worker) calls `sync_odds`.

Usage (from backend/):
    uv run python -m app.jobs.sync_odds                     # every FIXTURES_COMPETITIONS
    uv run python -m app.jobs.sync_odds --competition PD

Each call costs credits (1 per competition with regions=eu, markets=h2h) out of the
500/month free plan; the QuotaBudget refuses calls beyond ODDS_MONTHLY_BUDGET.
Run the fixtures sync first: odds are only attached to events that already exist.
"""

import argparse
import sys

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.jobs.sync_fixtures import EXIT_FAILED, EXIT_NOT_CONFIGURED, EXIT_OK
from app.models.enums import IngestionStatus, QuotaPeriod
from app.services.fixtures_provider import ProviderNotConfiguredError
from app.services.ingestion.odds_job import run_odds_ingestion
from app.services.odds_provider import get_odds_provider
from app.services.quota import QuotaPolicy

log = get_logger(__name__)


def odds_quota_policy(settings: Settings) -> QuotaPolicy:
    return QuotaPolicy(
        QuotaPeriod.MONTH, settings.odds_monthly_budget, reserve=settings.odds_credit_reserve
    )


def sync_odds(
    settings: Settings,
    session_factory: sessionmaker[Session],
    competitions: list[str] | None = None,
) -> int:
    try:
        provider = get_odds_provider(settings)
    except ProviderNotConfiguredError as exc:
        log.error("odds_provider_not_configured", error=str(exc))
        return EXIT_NOT_CONFIGURED
    ok = True
    try:
        for code in competitions or settings.fixtures_competitions:
            outcome = run_odds_ingestion(
                session_factory,
                provider,
                code,
                policy=odds_quota_policy(settings),
                expected_cost=provider.expected_cost(),
            )
            ok = ok and outcome.status is IngestionStatus.SUCCESS
    finally:
        provider.close()
    return EXIT_OK if ok else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync 1X2 odds from The Odds API")
    parser.add_argument("--competition", action="append", help="internal code, e.g. PD")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    from app.db.session import SessionLocal

    return sync_odds(settings, SessionLocal, args.competition)


if __name__ == "__main__":
    sys.exit(main())
