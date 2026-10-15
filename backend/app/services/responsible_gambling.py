"""Responsible-gambling rules: spending limits and self-exclusion.

Counting rule (QA F5): a bet counts against the limit period in which it was LOGGED here
(`created_at`, set by the server), never by `placed_at` (user-supplied: back-dating a bet
must not escape today's limit). Editing a bet re-checks the period it was logged in.
Void bets don't count (the money came back).

Changing a limit (rows are never edited, only superseded):
- Lowering (or a first limit): effective immediately; the old row is superseded now.
- Raising: the new row takes effect after a cooling-off period; the old, lower limit
  keeps applying until then (old.superseded_at = new.effective_from).
Periods are calendar periods in UTC (day; ISO week from Monday; month). Rolling vs
calendar windows and the cooling-off length depend on the jurisdiction (still to be
decided): these are conservative defaults.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Bet, SelfExclusion, SpendingLimit
from app.models.enums import BetStatus, LimitPeriod


def period_bounds(at: datetime, period: LimitPeriod) -> tuple[datetime, datetime]:
    day = at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if period is LimitPeriod.DAILY:
        return day, day + timedelta(days=1)
    if period is LimitPeriod.WEEKLY:
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    start = day.replace(day=1)
    return start, (start + timedelta(days=32)).replace(day=1)


def active_self_exclusion(s: Session, user_id: int, now: datetime) -> SelfExclusion | None:
    return s.scalar(
        select(SelfExclusion).where(
            SelfExclusion.user_id == user_id,
            SelfExclusion.started_at <= now,
            or_(SelfExclusion.ends_at.is_(None), SelfExclusion.ends_at > now),
        )
    )


def limits_in_force(s: Session, user_id: int, currency: str, at: datetime) -> list[SpendingLimit]:
    return list(
        s.scalars(
            select(SpendingLimit).where(
                SpendingLimit.user_id == user_id,
                SpendingLimit.currency == currency,
                SpendingLimit.effective_from <= at,
                or_(SpendingLimit.superseded_at.is_(None), SpendingLimit.superseded_at > at),
            )
        )
    )


def staked_in_period(
    s: Session,
    user_id: int,
    currency: str,
    start: datetime,
    end: datetime,
    exclude_bet_id: int | None = None,
) -> Decimal:
    query = select(Bet.stake).where(
        Bet.user_id == user_id,
        Bet.currency == currency,
        Bet.status != BetStatus.VOID,
        Bet.created_at >= start,
        Bet.created_at < end,
    )
    if exclude_bet_id is not None:
        query = query.where(Bet.id != exclude_bet_id)
    return sum(s.scalars(query).all(), Decimal("0"))


@dataclass(frozen=True)
class LimitBreach:
    period: LimitPeriod
    limit: Decimal
    already_staked: Decimal
    currency: str


def spending_limit_breach(
    s: Session,
    user_id: int,
    stake: Decimal,
    currency: str,
    at: datetime,
    exclude_bet_id: int | None = None,
) -> LimitBreach | None:
    """First limit in force at `at` that `stake` (logged at `at`) would exceed."""
    for limit in limits_in_force(s, user_id, currency, at):
        start, end = period_bounds(at, limit.period)
        already = staked_in_period(s, user_id, currency, start, end, exclude_bet_id)
        if already + stake > limit.amount:
            return LimitBreach(limit.period, limit.amount, already, currency)
    return None


def set_spending_limit(
    s: Session,
    user_id: int,
    period: LimitPeriod,
    amount: Decimal,
    currency: str,
    now: datetime,
    cooling_off: timedelta,
) -> SpendingLimit:
    current = [lim for lim in limits_in_force(s, user_id, currency, now) if lim.period is period]
    # A pending (not yet effective) change for this period is replaced by the new request.
    for pending in s.scalars(
        select(SpendingLimit).where(
            SpendingLimit.user_id == user_id,
            SpendingLimit.currency == currency,
            SpendingLimit.period == period,
            SpendingLimit.effective_from > now,
            SpendingLimit.superseded_at.is_(None),
        )
    ):
        pending.superseded_at = now
    raising = any(amount > lim.amount for lim in current)
    effective_from = now + cooling_off if raising else now
    for lim in current:
        lim.superseded_at = effective_from
    new = SpendingLimit(
        user_id=user_id,
        period=period,
        amount=amount,
        currency=currency,
        requested_at=now,
        effective_from=effective_from,
    )
    s.add(new)
    return new


@dataclass(frozen=True)
class LimitStatus:
    period: LimitPeriod
    amount: Decimal
    currency: str
    used: Decimal
    remaining: Decimal
    period_ends_at: datetime
    pending_amount: Decimal | None  # a raise waiting for its cooling-off
    pending_effective_from: datetime | None


def limit_status(s: Session, user_id: int, currency: str, now: datetime) -> list[LimitStatus]:
    out = []
    for lim in sorted(limits_in_force(s, user_id, currency, now), key=lambda x: x.period.value):
        start, end = period_bounds(now, lim.period)
        used = staked_in_period(s, user_id, currency, start, end)
        pending = s.scalar(
            select(SpendingLimit).where(
                SpendingLimit.user_id == user_id,
                SpendingLimit.currency == currency,
                SpendingLimit.period == lim.period,
                SpendingLimit.effective_from > now,
                SpendingLimit.superseded_at.is_(None),
            )
        )
        out.append(
            LimitStatus(
                period=lim.period,
                amount=lim.amount,
                currency=currency,
                used=used,
                remaining=max(lim.amount - used, Decimal("0")),
                period_ends_at=end,
                pending_amount=pending.amount if pending else None,
                pending_effective_from=pending.effective_from if pending else None,
            )
        )
    return out
