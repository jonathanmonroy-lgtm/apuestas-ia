from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.enums import Selection
from app.services.odds_summary import Quote, implied_probability, median, summarize

H, D, A = Selection.HOME, Selection.DRAW, Selection.AWAY
T0 = datetime(2026, 10, 14, 23, 5, tzinfo=UTC)
STALE = timedelta(hours=4)


def book(name: str, home: str, draw: str, away: str, at: datetime = T0) -> list[Quote]:
    return [
        Quote(name, H, Decimal(home), at),
        Quote(name, D, Decimal(draw), at),
        Quote(name, A, Decimal(away), at),
    ]


# Real prices recorded 2026-10-14 for Deportivo - Levante (4 bookmakers)
REAL = (
    book("pinnacle", "2.12", "3.52", "3.61")
    + book("williamhill", "2.05", "3.3", "3.3")
    + book("unibet_fr", "2.0", "3.3", "3.2")
    + book("betfair_ex_eu", "2.18", "3.6", "3.7")
)


def test_median_odd_even_and_empty():
    assert median([Decimal("3"), Decimal("1"), Decimal("2")]) == Decimal("2")
    assert median([Decimal("2.05"), Decimal("2.12")]) == Decimal("2.085")
    with pytest.raises(ValueError):
        median([])


def test_summary_of_real_prices():
    s = summarize(REAL, STALE)
    assert s.median_price == {H: Decimal("2.085"), D: Decimal("3.41"), A: Decimal("3.455")}
    assert s.n_bookmakers == 4


def test_implied_probability_of_real_medians():
    """Checked by hand with exact fractions: 0.451486, 0.276055, 0.272459; margin 6.23%."""
    ip = implied_probability({H: Decimal("2.085"), D: Decimal("3.41"), A: Decimal("3.455")})
    assert ip.probability == {H: Decimal("0.4515"), D: Decimal("0.2761"), A: Decimal("0.2725")}
    assert ip.overround == Decimal("0.0623")


def test_only_latest_quote_per_bookmaker_counts():
    newer = T0 + timedelta(hours=1)
    s = summarize(
        book("pinnacle", "2.12", "3.52", "3.61") + book("pinnacle", "2.5", "3.4", "3.0", newer),
        STALE,
    )
    assert s.median_price[H] == Decimal("2.5")
    assert s.n_bookmakers == 1
    assert s.as_of == newer


def test_incomplete_bookmaker_is_ignored():
    partial = [Quote("x", H, Decimal("9.0"), T0)]  # no draw/away
    s = summarize(REAL + partial, STALE)
    assert s.n_bookmakers == 4
    assert s.median_price[H] == Decimal("2.085")


def test_bookmaker_that_stopped_updating_is_ignored():
    old = book("sleepy", "5.0", "5.0", "5.0", T0 - timedelta(hours=5))
    s = summarize(REAL + old, STALE)
    assert s.n_bookmakers == 4
    assert s.oldest_quote_at == T0


def test_no_quotes_no_summary():
    assert summarize([], STALE) is None
