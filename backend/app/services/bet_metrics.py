"""Bet settlement and tracker metrics. Pure functions, exact Decimal arithmetic.

Definitions (shown to the user next to each number):
- Settled = won or lost. VOID bets returned the stake: they don't count in ROI, win rate,
  average stake, staked total or the evolution series (only in the counts).
- staked = sum of stakes of settled bets; returned = sum of their payouts (stake included).
- profit = returned - staked; ROI = profit / staked; win rate = won / settled.
- avg_stake = staked / settled.
- bankroll = starting bankroll + profit; available = bankroll - stakes of pending bets.
Ratios are None (not 0) when there is nothing to divide by: "no data" is not "0%".
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from app.models.enums import BetStatus

CENT = Decimal("0.01")
RATIO = Decimal("0.0001")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def default_payout(status: BetStatus, stake: Decimal, odds: Decimal) -> Decimal | None:
    """What a single returns. Bookmakers round winnings to the cent (half up)."""
    if status is BetStatus.WON:
        return money(stake * odds)
    if status is BetStatus.LOST:
        return Decimal("0.00")
    if status is BetStatus.VOID:
        return money(stake)
    return None


@dataclass(frozen=True)
class BetRow:
    id: int
    status: BetStatus
    stake: Decimal
    payout: Decimal | None
    settled_at: datetime | None


@dataclass(frozen=True)
class SeriesPoint:
    bet_id: int
    settled_at: datetime
    profit: Decimal
    cumulative_profit: Decimal
    bankroll: Decimal | None


@dataclass(frozen=True)
class Metrics:
    n_bets: int
    n_pending: int
    n_won: int
    n_lost: int
    n_void: int
    staked: Decimal
    returned: Decimal
    profit: Decimal
    roi: Decimal | None
    win_rate: Decimal | None
    avg_stake: Decimal | None
    pending_stake: Decimal
    starting_bankroll: Decimal | None
    bankroll: Decimal | None
    available: Decimal | None
    series: list[SeriesPoint]


def compute_metrics(bets: Iterable[BetRow], starting_bankroll: Decimal | None) -> Metrics:
    bets = list(bets)
    settled = sorted(
        (b for b in bets if b.status in (BetStatus.WON, BetStatus.LOST)),
        key=lambda b: (b.settled_at, b.id),
    )
    won = [b for b in settled if b.status is BetStatus.WON]
    pending = [b for b in bets if b.status is BetStatus.PENDING]

    staked = sum((b.stake for b in settled), Decimal("0"))
    returned = sum((b.payout or Decimal("0") for b in settled), Decimal("0"))
    profit = returned - staked
    pending_stake = sum((b.stake for b in pending), Decimal("0"))

    series = []
    cumulative = Decimal("0")
    for b in settled:
        delta = (b.payout or Decimal("0")) - b.stake
        cumulative += delta
        series.append(
            SeriesPoint(
                bet_id=b.id,
                settled_at=b.settled_at,
                profit=money(delta),
                cumulative_profit=money(cumulative),
                bankroll=money(starting_bankroll + cumulative)
                if starting_bankroll is not None
                else None,
            )
        )

    def ratio(num: Decimal, den: Decimal) -> Decimal | None:
        return (num / den).quantize(RATIO, ROUND_HALF_EVEN) if den else None

    bankroll = money(starting_bankroll + profit) if starting_bankroll is not None else None
    return Metrics(
        n_bets=len(bets),
        n_pending=len(pending),
        n_won=len(won),
        n_lost=len(settled) - len(won),
        n_void=sum(1 for b in bets if b.status is BetStatus.VOID),
        staked=money(staked),
        returned=money(returned),
        profit=money(profit),
        roi=ratio(profit, staked),
        win_rate=ratio(Decimal(len(won)), Decimal(len(settled))),
        avg_stake=money(staked / len(settled)) if settled else None,
        pending_stake=money(pending_stake),
        starting_bankroll=money(starting_bankroll) if starting_bankroll is not None else None,
        bankroll=bankroll,
        available=money(bankroll - pending_stake) if bankroll is not None else None,
        series=series,
    )
