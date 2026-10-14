"""News sync: manual entry point; the worker (app.worker) calls `sync_news`.

Usage (from backend/):
    uv run python -m app.jobs.sync_news

One NewsAPI request per run (free plan: 100/day). Articles are only linked to teams
that exist in the calendar, so run the fixtures sync first.
"""

import sys

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.jobs.sync_fixtures import EXIT_FAILED, EXIT_NOT_CONFIGURED, EXIT_OK
from app.models.enums import IngestionStatus, QuotaPeriod
from app.services.fixtures_provider import ProviderNotConfiguredError
from app.services.ingestion.news_job import run_news_ingestion
from app.services.news_provider import get_news_provider
from app.services.quota import QuotaPolicy

log = get_logger(__name__)


def sync_news(settings: Settings, session_factory: sessionmaker[Session]) -> int:
    try:
        provider = get_news_provider(settings)
    except ProviderNotConfiguredError as exc:
        log.error("news_provider_not_configured", error=str(exc))
        return EXIT_NOT_CONFIGURED
    try:
        outcome = run_news_ingestion(
            session_factory,
            provider,
            policy=QuotaPolicy(QuotaPeriod.DAY, settings.newsapi_daily_budget),
        )
    finally:
        provider.close()
    return EXIT_OK if outcome.status is IngestionStatus.SUCCESS else EXIT_FAILED


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    from app.db.session import SessionLocal

    return sync_news(settings, SessionLocal)


if __name__ == "__main__":
    sys.exit(main())
