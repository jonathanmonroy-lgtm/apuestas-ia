"""Odds summary for an event: hard data (median price) and statistical analysis
(implied probability) kept apart, both in exact Decimal arithmetic.

Which prices count: the latest quote of each bookmaker that offers the full 1X2 triple,
ignoring bookmakers whose latest quote is older than `stale_after` relative to the
freshest one (a book that stopped updating would drag the median with an old price).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from app.models.enums import Selection

PROB_QUANTUM = Decimal("0.0001")
SELECTIONS = (Selection.HOME, Selection.DRAW, Selection.AWAY)


@dataclass(frozen=True)
class Quote:
    bookmaker: str
    selection: Selection
    price: Decimal
    captured_at: datetime


@dataclass(frozen=True)
class OddsSummary:
    median_price: dict[Selection, Decimal]
    n_bookmakers: int
    as_of: datetime  # freshest quote used
    oldest_quote_at: datetime  # oldest quote used


@dataclass(frozen=True)
class ImpliedProbability:
    probability: dict[Selection, Decimal]  # margin removed, sums to ~1
    overround: Decimal  # sum of 1/price - 1: the bookmakers' margin on the median prices


def median(values: list[Decimal]) -> Decimal:
    if not values:
        raise ValueError("median of empty list")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def summarize(quotes: Iterable[Quote], stale_after: timedelta) -> OddsSummary | None:
    latest: dict[tuple[str, Selection], Quote] = {}
    for q in quotes:
        key = (q.bookmaker, q.selection)
        if key not in latest or q.captured_at > latest[key].captured_at:
            latest[key] = q

    books: dict[str, dict[Selection, Quote]] = {}
    for (book, sel), q in latest.items():
        books.setdefault(book, {})[sel] = q
    complete = {b: sels for b, sels in books.items() if set(sels) == set(SELECTIONS)}
    if not complete:
        return None

    freshest = max(q.captured_at for sels in complete.values() for q in sels.values())
    fresh = {
        b: sels
        for b, sels in complete.items()
        if min(q.captured_at for q in sels.values()) >= freshest - stale_after
    }
    used = [q for sels in fresh.values() for q in sels.values()]
    return OddsSummary(
        median_price={s: median([sels[s].price for sels in fresh.values()]) for s in SELECTIONS},
        n_bookmakers=len(fresh),
        as_of=freshest,
        oldest_quote_at=min(q.captured_at for q in used),
    )


def implied_probability(median_price: dict[Selection, Decimal]) -> ImpliedProbability:
    inverse = {s: Decimal(1) / median_price[s] for s in SELECTIONS}
    total = sum(inverse.values(), Decimal(0))
    return ImpliedProbability(
        probability={
            s: (inverse[s] / total).quantize(PROB_QUANTUM, ROUND_HALF_EVEN) for s in SELECTIONS
        },
        overround=(total - 1).quantize(PROB_QUANTUM, ROUND_HALF_EVEN),
    )
