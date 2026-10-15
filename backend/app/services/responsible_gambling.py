"""Responsible-gambling checks applied when the user logs a bet.

Periods are calendar periods in UTC (day; ISO week from Monday; month). The exact rules
(rolling vs calendar windows, cooling-off for raising a limit) depend on the jurisdiction
and are still to be decided: this is the conservative default.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Bet, SelfExclusion, SpendingLimit
from app.models.enums import BetStatus, LimitPeriod


def period_start(now: datetime, period: LimitPeriod) -> datetime:
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if period is LimitPeriod.DAILY:
        return day
    if period is LimitPeriod.WEEKLY:
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def active_self_exclusion(s: Session, user_id: int, now: datetime) -> SelfExclusion | None:
    return s.scalar(
        select(SelfExclusion).where(
            SelfExclusion.user_id == user_id,
            SelfExclusion.started_at <= now,
            or_(SelfExclusion.ends_at.is_(None), SelfExclusion.ends_at > now),
        )
    )


@dataclass(frozen=True)
class LimitBreach:
    period: LimitPeriod
    limit: Decimal
    already_staked: Decimal
    currency: str


def spending_limit_breach(
    s: Session, user_id: int, stake: Decimal, currency: str, now: datetime
) -> LimitBreach | None:
    """The first active limit (same currency) that this new stake would exceed."""
    limits = s.scalars(
        select(SpendingLimit).where(
            SpendingLimit.user_id == user_id,
            SpendingLimit.currency == currency,
            SpendingLimit.superseded_at.is_(None),
            SpendingLimit.effective_from <= now,
        )
    ).all()
    for limit in limits:
        start = period_start(now, limit.period)
        stakes = s.scalars(
            select(Bet.stake).where(
                Bet.user_id == user_id,
                Bet.currency == currency,
                Bet.status != BetStatus.VOID,  # a void bet gave the money back
                Bet.placed_at >= start,
            )
        ).all()
        already = sum(stakes, Decimal("0"))
        if already + stake > limit.amount:
            return LimitBreach(limit.period, limit.amount, already, currency)
    return None
