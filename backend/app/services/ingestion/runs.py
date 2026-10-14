"""IngestionRun lifecycle, shared by every ingestion job.

The run row is written in its own short transactions so that it survives even when the
data transaction is rolled back (that's the point: keep the raw JSON of failures).
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models import IngestionRun
from app.models.enums import IngestionStatus, Provider
from app.services.errors import ProviderError
from app.services.http import RawResponse

log = get_logger(__name__)


@dataclass
class RunOutcome:
    run_id: int
    status: IngestionStatus
    records_written: int = 0
    error: str | None = None


class RunRecorder:
    def __init__(self, session_factory: sessionmaker[Session], source: Provider, job: str):
        self._sf = session_factory
        self.source = source
        self.job = job
        self._t0 = time.monotonic()
        with self._sf() as s:
            run = IngestionRun(source=source, job=job, status=IngestionStatus.RUNNING)
            s.add(run)
            s.commit()
            self.run_id = run.id
        log.info("ingestion_started", source=source.value, job=job, run_id=self.run_id)

    def save_raw(self, raw: RawResponse, quota_remaining: int | None) -> None:
        with self._sf() as s:
            run = s.get_one(IngestionRun, self.run_id)
            run.request_url = raw.url
            run.http_status = raw.http_status
            run.attempts = raw.attempts
            run.raw_payload = raw.payload
            run.quota_remaining = quota_remaining
            s.commit()

    def finish(
        self,
        status: IngestionStatus,
        *,
        records: int = 0,
        error: ProviderError | Exception | None = None,
    ) -> RunOutcome:
        message = f"{error.__class__.__name__}: {error}" if error else None
        with self._sf() as s:
            run = s.get_one(IngestionRun, self.run_id)
            run.status = status
            run.finished_at = datetime.now(UTC)
            run.records_written = records
            run.error_message = message
            if isinstance(error, ProviderError):
                run.http_status = run.http_status or error.http_status
                run.attempts = max(run.attempts, error.attempts)
            s.commit()

        fields = dict(
            source=self.source.value,
            job=self.job,
            run_id=self.run_id,
            status=status.value,
            records=records,
            duration_ms=round((time.monotonic() - self._t0) * 1000),
        )
        if status is IngestionStatus.SUCCESS:
            log.info("ingestion_finished", **fields)
        elif status is IngestionStatus.SKIPPED_NO_BUDGET:
            log.warning("ingestion_skipped", reason=message, **fields)
        elif status is IngestionStatus.AUTH_ERROR:
            # Needs a human (bad key / plan). TODO: route `alert=True` to a real channel.
            log.error("ingestion_failed", alert=True, error=message, **fields)
        else:
            log.error("ingestion_failed", error=message, **fields)
        return RunOutcome(self.run_id, status, records, message)


def last_success_at(session: Session, source: Provider, job: str) -> datetime | None:
    """When `job` last finished successfully for `source` (None if it never did)."""
    return session.scalar(
        select(func.max(IngestionRun.finished_at)).where(
            IngestionRun.source == source,
            IngestionRun.job == job,
            IngestionRun.status == IngestionStatus.SUCCESS,
        )
    )
