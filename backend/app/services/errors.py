"""Provider failure taxonomy. Each kind maps to a distinct IngestionStatus and policy."""

from datetime import datetime

from app.models.enums import IngestionStatus


class ProviderError(Exception):
    """Base class. `status` is what the IngestionRun gets marked with."""

    status: IngestionStatus = IngestionStatus.FAILED

    def __init__(self, message: str, *, http_status: int | None = None, attempts: int = 0):
        super().__init__(message)
        self.http_status = http_status
        self.attempts = attempts


class ThrottledError(ProviderError):
    """Transient rate limiting (e.g. per-minute). Retried with backoff; raised after giving up."""

    status = IngestionStatus.THROTTLED


class QuotaExhaustedError(ProviderError):
    """Daily/monthly quota used up. Never retried; provider is paused until `retry_after`."""

    status = IngestionStatus.QUOTA_EXHAUSTED

    def __init__(self, message: str, *, retry_after: datetime | None = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class AuthError(ProviderError):
    """Invalid key, or resource not covered by our plan. Never retried; needs a human."""

    status = IngestionStatus.AUTH_ERROR


class ProviderUnavailableError(ProviderError):
    """5xx / network errors that persisted after retries."""


class FormatError(ProviderError):
    """Response doesn't match what the normalizer expects (provider changed its format)."""


class BudgetRefusedError(ProviderError):
    """QuotaBudget refused the call before it was made (no request sent)."""

    status = IngestionStatus.SKIPPED_NO_BUDGET
