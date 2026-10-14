"""Background worker: runs scheduled ingestion jobs in its OWN process.

Never imported by the API (app.main): if a provider hangs or the scheduler crashes, the
API keeps serving the last stored data. Run it with:

    uv run python -m app.worker

One worker instance per deployment. APScheduler keeps jobs in memory, so two workers
would sync twice (the QuotaBudget would still cap the damage).
"""

from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.jobs.sync_fixtures import sync_fixtures
from app.jobs.sync_news import sync_news
from app.jobs.sync_odds import sync_odds
from app.models.enums import Provider
from app.services.ingestion.fixtures_job import JOB_FIXTURES
from app.services.ingestion.news_job import JOB_NEWS
from app.services.ingestion.odds_job import JOB_ODDS
from app.services.ingestion.retention import purge_raw_payloads
from app.services.ingestion.runs import last_success_at

log = get_logger(__name__)

FIXTURES_JOB_ID = "sync_fixtures"
ODDS_JOB_ID = "sync_odds"
NEWS_JOB_ID = "sync_news"
RETENTION_JOB_ID = "purge_raw_payloads"
# On a cold start, give the fixtures catch-up time to finish before odds look for events.
ODDS_STARTUP_DELAY = timedelta(minutes=10)


def is_stale(
    session_factory: sessionmaker[Session],
    source: Provider,
    job: str,
    max_age_hours: float,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(UTC)
    with session_factory() as s:
        last = last_success_at(s, source, job)
    return last is None or now - last > timedelta(hours=max_age_hours)


def fixtures_are_stale(
    settings: Settings, session_factory: sessionmaker[Session], now: datetime | None = None
) -> bool:
    return is_stale(
        session_factory,
        Provider(settings.fixtures_provider),
        JOB_FIXTURES,
        settings.fixtures_stale_after_hours,
        now,
    )


def build_scheduler(
    settings: Settings, session_factory: sessionmaker[Session]
) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=UTC)

    def run_fixtures() -> None:
        code = sync_fixtures(settings, session_factory)
        log.info("scheduled_job_done", job=FIXTURES_JOB_ID, exit_code=code)

    scheduler.add_job(
        run_fixtures,
        CronTrigger(
            hour=settings.fixtures_sync_hour_utc,
            minute=settings.fixtures_sync_minute,
            timezone=UTC,
        ),
        id=FIXTURES_JOB_ID,
        max_instances=1,  # never overlap with a slow previous run
        coalesce=True,  # missed several runs (worker down)? run once, not N times
        misfire_grace_time=3600,
    )
    # Catch up right away if the worker was down long enough for data to go stale.
    if fixtures_are_stale(settings, session_factory):
        log.warning("fixtures_stale_on_startup", action="sync_now")
        scheduler.add_job(run_fixtures, id=f"{FIXTURES_JOB_ID}_catchup", max_instances=1)

    def run_odds() -> None:
        code = sync_odds(settings, session_factory)
        log.info("scheduled_job_done", job=ODDS_JOB_ID, exit_code=code)

    odds_stale = is_stale(
        session_factory, Provider.THE_ODDS_API, JOB_ODDS, settings.odds_stale_after_hours
    )
    scheduler.add_job(
        run_odds,
        IntervalTrigger(hours=settings.odds_sync_interval_hours, timezone=UTC),
        id=ODDS_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
        # Stale odds: first run soon (after the fixtures catch-up), else wait an interval.
        **({"next_run_time": datetime.now(UTC) + ODDS_STARTUP_DELAY} if odds_stale else {}),
    )

    def run_news() -> None:
        code = sync_news(settings, session_factory)
        log.info("scheduled_job_done", job=NEWS_JOB_ID, exit_code=code)

    news_stale = is_stale(
        session_factory, Provider.NEWSAPI, JOB_NEWS, settings.news_stale_after_hours
    )
    scheduler.add_job(
        run_news,
        IntervalTrigger(hours=settings.news_sync_interval_hours, timezone=UTC),
        id=NEWS_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
        # After downtime, fetch soon (after the fixtures catch-up: news are linked to
        # teams that must exist). The free plan can't page back, so waiting loses more.
        **({"next_run_time": datetime.now(UTC) + ODDS_STARTUP_DELAY} if news_stale else {}),
    )

    def run_retention() -> None:
        purge_raw_payloads(session_factory, settings.raw_payload_retention_days)

    scheduler.add_job(
        run_retention,
        CronTrigger(hour=3, minute=30, timezone=UTC),
        id=RETENTION_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=6 * 3600,
    )
    return scheduler


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    from app.db.session import SessionLocal

    scheduler = build_scheduler(settings, SessionLocal)
    log.info(
        "worker_started",
        jobs={j.id: str(j.trigger) for j in scheduler.get_jobs()},
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker_stopped")


if __name__ == "__main__":
    main()
