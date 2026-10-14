"""Budget-guarded provider call, shared by every ingestion job.

1. QuotaBudget decides whether we can afford the call (if not: run SKIPPED, no request).
2. Call the provider. Any exception closes the run (never left RUNNING).
3. Charge the budget with what the call really cost and store the raw JSON on the run.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models.enums import IngestionStatus, Provider
from app.services.errors import BudgetRefusedError, ProviderError, QuotaExhaustedError
from app.services.http import RawResponse
from app.services.ingestion.runs import RunOutcome, RunRecorder
from app.services.quota import QuotaBudget, QuotaPolicy

log = get_logger(__name__)


def fetch_with_budget(
    session_factory: sessionmaker[Session],
    recorder: RunRecorder,
    source: Provider,
    policy: QuotaPolicy,
    fetch: Callable[[], RawResponse],
    *,
    expected_cost: int = 1,
    cost_of: Callable[[RawResponse], int] = lambda raw: max(1, raw.attempts),
    remaining_of: Callable[[RawResponse], int | None] = lambda raw: None,
    redact: Callable[[Any], Any] | None = None,
) -> RawResponse | RunOutcome:
    """Returns the raw response, or the finished RunOutcome if we must stop here."""
    with session_factory() as s:
        allowed, reason = QuotaBudget(s, source, policy).can_spend(expected_cost)
        s.commit()
    if not allowed:
        return recorder.finish(IngestionStatus.SKIPPED_NO_BUDGET, error=BudgetRefusedError(reason))

    try:
        raw = fetch()
    except Exception as exc:  # ProviderError, or anything unexpected
        attempts = exc.attempts if isinstance(exc, ProviderError) else 1
        with session_factory() as s:
            budget = QuotaBudget(s, source, policy)
            # Every retry was a real request: charge them all (overcounting is the safe side).
            budget.record_call(cost=max(1, attempts))
            if isinstance(exc, QuotaExhaustedError):
                budget.mark_exhausted(exc.retry_after)
            s.commit()
        if not isinstance(exc, ProviderError):
            log.exception("ingestion_fetch_unexpected_error", run_id=recorder.run_id)
        status = exc.status if isinstance(exc, ProviderError) else IngestionStatus.FAILED
        return recorder.finish(status, error=exc)

    remaining = remaining_of(raw)
    with session_factory() as s:
        QuotaBudget(s, source, policy).record_call(cost=cost_of(raw), remaining=remaining)
        s.commit()
    # What we keep for audit can be less than what we use (e.g. third-party text).
    recorder.save_raw(replace(raw, payload=redact(raw.payload)) if redact else raw, remaining)
    return raw
