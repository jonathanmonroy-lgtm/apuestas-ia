"""QuotaBudget: decides *before* calling a provider whether we can afford the request.

Counters live in `provider_quota` so they survive restarts and are shared by every
process. When the provider reports its own remaining quota (response headers) we trust
that over our local count.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import ProviderQuota
from app.models.enums import Provider, QuotaPeriod

log = get_logger(__name__)


@dataclass(frozen=True)
class QuotaPolicy:
    period: QuotaPeriod
    limit: int  # our self-imposed budget, <= the provider's real limit
    # Credits that must stay untouched per the PROVIDER's own count (it also sees calls
    # made outside our jobs, e.g. manual tests), so the worker never drains the account.
    reserve: int = 0


def period_start(now: datetime, period: QuotaPeriod) -> datetime:
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day if period is QuotaPeriod.DAY else day.replace(day=1)


def next_reset(now: datetime, period: QuotaPeriod) -> datetime:
    start = period_start(now, period)
    if period is QuotaPeriod.DAY:
        return start + timedelta(days=1)
    return (start + timedelta(days=32)).replace(day=1)


class QuotaBudget:
    def __init__(self, session: Session, source: Provider, policy: QuotaPolicy) -> None:
        self.session = session
        self.source = source
        self.policy = policy

    def _row(self, now: datetime) -> ProviderQuota:
        row = self.session.get(ProviderQuota, self.source)
        start = period_start(now, self.policy.period)
        if row is None:
            row = ProviderQuota(
                source=self.source,
                period=self.policy.period,
                limit=self.policy.limit,
                used=0,
                period_start=start,
            )
            self.session.add(row)
        elif row.period_start < start:  # new day/month: counters roll over
            row.used = 0
            row.remaining = None
            row.period_start = start
        row.period = self.policy.period
        row.limit = self.policy.limit
        return row

    def can_spend(self, cost: int = 1, now: datetime | None = None) -> tuple[bool, str]:
        now = now or datetime.now(UTC)
        row = self._row(now)
        if row.exhausted_until is not None and row.exhausted_until > now:
            return False, f"provider paused until {row.exhausted_until.isoformat()}"
        if row.used + cost > row.limit:
            return False, f"budget {row.used}/{row.limit} per {row.period.value}"
        if row.remaining is not None and row.remaining - cost < self.policy.reserve:
            return False, (
                f"provider reports {row.remaining} remaining (reserve {self.policy.reserve})"
            )
        return True, "ok"

    def record_call(
        self, cost: int = 1, remaining: int | None = None, now: datetime | None = None
    ) -> None:
        now = now or datetime.now(UTC)
        row = self._row(now)
        row.used += cost
        if remaining is not None:
            row.remaining = remaining
            if remaining <= 0:
                row.exhausted_until = next_reset(now, self.policy.period)

    def mark_exhausted(self, until: datetime | None = None, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        row = self._row(now)
        row.exhausted_until = until or next_reset(now, self.policy.period)
        log.error(
            "provider_quota_exhausted",
            source=self.source.value,
            exhausted_until=row.exhausted_until.isoformat(),
        )
