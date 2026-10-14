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
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.jobs.sync_fixtures import sync_fixtures
from app.models.enums import Provider
from app.services.ingestion.fixtures_job import JOB_FIXTURES
from app.services.ingestion.runs import last_success_at

log = get_logger(__name__)

FIXTURES_JOB_ID = "sync_fixtures"


def fixtures_are_stale(
    settings: Settings, session_factory: sessionmaker[Session], now: datetime | None = None
) -> bool:
    now = now or datetime.now(UTC)
    with session_factory() as s:
        last = last_success_at(s, Provider(settings.fixtures_provider), JOB_FIXTURES)
    return last is None or now - last > timedelta(hours=settings.fixtures_stale_after_hours)


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
    return scheduler


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    from app.db.session import SessionLocal

    scheduler = build_scheduler(settings, SessionLocal)
    job = scheduler.get_job(FIXTURES_JOB_ID)
    log.info("worker_started", jobs=[j.id for j in scheduler.get_jobs()], trigger=str(job.trigger))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker_stopped")


if __name__ == "__main__":
    main()
