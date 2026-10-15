"""Multi-country matches of the day (API-Football): manual entry point; the worker calls
`sync_day`.

Usage (from backend/):
    uv run python -m app.jobs.sync_day

One pass = fixtures for yesterday/today/tomorrow (Colombia's days: 3 requests) + the 3
odds pages the free plan allows for today and tomorrow (6 requests). Waits between
requests: the free plan allows 10 per minute.
"""

import sys
import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.jobs.sync_fixtures import EXIT_FAILED, EXIT_NOT_CONFIGURED, EXIT_OK
from app.models.enums import IngestionStatus, QuotaPeriod
from app.services.fixtures_provider import ProviderNotConfiguredError
from app.services.fixtures_provider.api_football import FREE_ODDS_MAX_PAGE, ApiFootballProvider
from app.services.ingestion.day_job import run_day_fixtures, run_day_odds_page
from app.services.quota import QuotaPolicy

log = get_logger(__name__)
PAUSE_SECONDS = 7.0  # 10 requests/minute on the free plan


def local_today(settings: Settings, now: datetime | None = None) -> date:
    now = now or datetime.now(UTC)
    return (now + timedelta(hours=settings.local_utc_offset_hours)).date()


def days_in_free_window(days: list[date], now: datetime | None = None) -> list[date]:
    """The free plan only accepts dates within UTC yesterday..tomorrow (verified: "try from
    2026-10-14 to 2026-10-16" at 03:00Z on the 15th). A Colombian day outside it is skipped,
    not wasted as a refused request."""
    utc_today = (now or datetime.now(UTC)).date()
    return [d for d in days if abs((d - utc_today).days) <= 1]


def api_football(settings: Settings) -> ApiFootballProvider:
    key = settings.api_football_key.get_secret_value().strip() if settings.api_football_key else ""
    if not key:
        raise ProviderNotConfiguredError("API_FOOTBALL_KEY is not set")
    return ApiFootballProvider(key)


def sync_day(
    settings: Settings, session_factory: sessionmaker[Session], pause: float = PAUSE_SECONDS
) -> int:
    try:
        provider = api_football(settings)
    except ProviderNotConfiguredError as exc:
        log.error("api_football_not_configured", error=str(exc))
        return EXIT_NOT_CONFIGURED
    policy = QuotaPolicy(QuotaPeriod.DAY, settings.api_football_scheduled_budget)
    skip = frozenset(settings.api_football_skip_leagues)
    today = local_today(settings)
    ok = True
    try:
        days = [today - timedelta(days=1), today, today + timedelta(days=1)]
        for day in days_in_free_window(days):
            outcome = run_day_fixtures(session_factory, provider, day, policy=policy,
                                       skip_league_ids=skip)  # fmt: skip
            ok = ok and outcome.status is IngestionStatus.SUCCESS
            time.sleep(pause)
        for day in days_in_free_window([today, today + timedelta(days=1)]):
            for page in range(1, FREE_ODDS_MAX_PAGE + 1):
                outcome = run_day_odds_page(session_factory, provider, day, page, policy=policy)
                ok = ok and outcome.status is IngestionStatus.SUCCESS
                time.sleep(pause)
    finally:
        provider.close()
    return EXIT_OK if ok else EXIT_FAILED


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    from app.db.session import SessionLocal

    return sync_day(settings, SessionLocal)


if __name__ == "__main__":
    sys.exit(main())
