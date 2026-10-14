"""Retention for raw provider payloads kept on ingestion_run.

Raw JSON is kept to audit and re-process recent runs, not forever: it may contain
third-party material (news text) and grows every hour. After the retention window the
payload is dropped; the run row itself (status, counts, errors) is kept.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models import IngestionRun

log = get_logger(__name__)


def purge_raw_payloads(
    session_factory: sessionmaker[Session], days: int, now: datetime | None = None
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    with session_factory() as s, s.begin():
        result = s.execute(
            update(IngestionRun)
            .where(IngestionRun.started_at < cutoff, IngestionRun.raw_payload.is_not(None))
            .values(raw_payload=None)
        )
    log.info("raw_payloads_purged", rows=result.rowcount, older_than=cutoff.isoformat())
    return result.rowcount
