import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.models import IngestionRun
from app.models.enums import IngestionStatus, Provider
from app.services.ingestion.fixtures_job import JOB_FIXTURES
from app.worker import FIXTURES_JOB_ID, build_scheduler, fixtures_are_stale

SETTINGS = Settings(fixtures_provider="football_data", fixtures_stale_after_hours=30)


@pytest.fixture
def sf(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


def add_run(sf, finished_at: datetime, status=IngestionStatus.SUCCESS) -> None:
    with sf() as s:
        s.add(
            IngestionRun(
                source=Provider.FOOTBALL_DATA, job=JOB_FIXTURES, status=status,
                finished_at=finished_at,
            )
        )  # fmt: skip
        s.commit()


def test_daily_cron_at_configured_utc_time(sf):
    add_run(sf, datetime.now(UTC))  # fresh data: no catch-up job
    scheduler = build_scheduler(SETTINGS, sf)

    assert [j.id for j in scheduler.get_jobs()] == [FIXTURES_JOB_ID]
    job = scheduler.get_job(FIXTURES_JOB_ID)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("6", "15")
    assert job.max_instances == 1
    assert job.coalesce is True


def test_stale_data_schedules_catch_up_run(sf):
    add_run(sf, datetime.now(UTC) - timedelta(hours=31))
    jobs = {j.id for j in build_scheduler(SETTINGS, sf).get_jobs()}
    assert jobs == {FIXTURES_JOB_ID, f"{FIXTURES_JOB_ID}_catchup"}


def test_staleness_only_counts_successful_runs(sf):
    now = datetime(2026, 10, 14, 12, tzinfo=UTC)
    assert fixtures_are_stale(SETTINGS, sf, now)  # never ran
    add_run(sf, now - timedelta(hours=1), status=IngestionStatus.FAILED)
    assert fixtures_are_stale(SETTINGS, sf, now)
    add_run(sf, now - timedelta(hours=29))
    assert not fixtures_are_stale(SETTINGS, sf, now)


def test_api_process_never_loads_the_scheduler():
    """The scheduler lives in its own process: importing the API must not pull it in."""
    code = (
        "import sys, app.main; "
        "print(any(m.startswith(('apscheduler', 'app.worker')) for m in sys.modules))"
    )
    out = subprocess.run(  # noqa: S603 - fixed command, no user input
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"
