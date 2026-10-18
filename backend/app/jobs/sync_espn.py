"""Multi-country matches of the day (ESPN, no key): manual entry point; the worker calls
`sync_espn`.

Usage (from backend/):
    uv run python -m app.jobs.sync_espn          # near pass, or wide if the window is stale
    uv run python -m app.jobs.sync_espn --wide   # the whole browsable window (-3..+7 days)

The worker runs ONE job: every pass is near (yesterday..tomorrow, live scores) unless the
last successful wide pass is older than ESPN_WIDE_INTERVAL_HOURS, then it is wide. Near
and wide passes therefore never overlap inside the worker (running this CLI by hand while
the worker runs can still overlap: don't).

One pass = the league catalogue (1 request) + one scoreboard per ESPN day. ESPN's days
are US Eastern (they start at 04:00Z in summer time), Colombia's start at 05:00Z: the
last hour of a Colombian day falls in the next ESPN day, so one more day is fetched.
ESPN publishes no limits; we pause between requests and keep a self-imposed daily budget.
"""

import argparse
import sys
import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.jobs.sync_day import local_today
from app.jobs.sync_fixtures import EXIT_FAILED, EXIT_OK
from app.models.enums import IngestionStatus, Provider, QuotaPeriod
from app.services.fixtures_provider.espn import EspnProvider
from app.services.ingestion.espn_job import (
    JOB_ESPN_DAY,
    JOB_ESPN_DAY_WIDE,
    run_espn_day,
    run_espn_leagues,
)
from app.services.ingestion.runs import RunOutcome, last_success_at
from app.services.quota import QuotaPolicy

log = get_logger(__name__)
PAUSE_SECONDS = 2.0  # be polite with an unofficial endpoint


def espn_days(first: date, last: date) -> list[date]:
    """ESPN days covering Colombia's days first..last (inclusive)."""
    return [first + timedelta(days=i) for i in range((last - first).days + 2)]


def window(settings: Settings, wide: bool, today: date) -> tuple[date, date]:
    if wide:
        return today - timedelta(days=settings.espn_days_back), today + timedelta(
            days=settings.espn_days_ahead
        )
    return today - timedelta(days=1), today + timedelta(days=1)


def wide_pass_due(
    settings: Settings, session_factory: sessionmaker[Session], now: datetime | None = None
) -> bool:
    with session_factory() as s:
        last = last_success_at(s, Provider.ESPN, JOB_ESPN_DAY_WIDE)
    now = now or datetime.now(UTC)
    return last is None or now - last >= timedelta(hours=settings.espn_wide_interval_hours)


def sync_espn(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    wide: bool | None = None,  # None: decide (wide when the window is stale)
    pause: float = PAUSE_SECONDS,
    provider: EspnProvider | None = None,
    today: date | None = None,
) -> int:
    if wide is None:
        wide = wide_pass_due(settings, session_factory)
    provider = provider or EspnProvider()
    policy = QuotaPolicy(QuotaPeriod.DAY, settings.espn_daily_budget)
    skip = frozenset(settings.espn_skip_leagues)
    first, last = window(settings, wide, today or local_today(settings))
    ok = True
    try:
        leagues = run_espn_leagues(session_factory, provider, policy=policy)
        if isinstance(leagues, RunOutcome):
            log.error("espn_leagues_unavailable", status=leagues.status.value)
            return EXIT_FAILED
        for day in espn_days(first, last):
            time.sleep(pause)
            outcome = run_espn_day(
                session_factory, provider, day, leagues, policy=policy, skip_league_slugs=skip,
                odds_refresh=timedelta(hours=settings.espn_odds_refresh_hours),
                job=JOB_ESPN_DAY_WIDE if wide else JOB_ESPN_DAY,
            )  # fmt: skip
            ok = ok and outcome.status is IngestionStatus.SUCCESS
            if outcome.status is IngestionStatus.SKIPPED_NO_BUDGET:
                break  # the rest would be refused too
    finally:
        provider.close()
    return EXIT_OK if ok else EXIT_FAILED


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wide", action="store_true", help="whole window, not just ±1 day")
    args = parser.parse_args()  # without --wide: decided by staleness, as in the worker
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    from app.db.session import SessionLocal

    return sync_espn(settings, SessionLocal, wide=True if args.wide else None)


if __name__ == "__main__":
    sys.exit(main())
