from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.enums import BetStatus
from app.services.bet_metrics import BetRow, compute_metrics, default_payout

D = Decimal
T0 = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
WON, LOST, VOID, PENDING = BetStatus.WON, BetStatus.LOST, BetStatus.VOID, BetStatus.PENDING


def bet(i, status, stake, odds="2.00", payout=None):
    stake, odds = D(stake), D(odds)
    if payout is None:
        payout = default_payout(status, stake, odds)
    settled = T0 + timedelta(hours=i) if status is not PENDING else None
    return BetRow(i, status, stake, D(payout) if payout is not None else None, settled)


# --- settlement ---


@pytest.mark.parametrize(
    ("status", "stake", "odds", "expected"),
    [
        (WON, "10", "1.95", D("19.50")),
        (WON, "7.33", "2.17", D("15.91")),  # 15.9061 -> rounded half up to the cent
        (WON, "0.05", "1.1", D("0.06")),  # 0.055 -> 0.06 (half up)
        (LOST, "10", "3.40", D("0.00")),
        (VOID, "12.50", "3.40", D("12.50")),  # stake back
        (PENDING, "10", "2", None),
    ],
)
def test_default_payout(status, stake, odds, expected):
    assert default_payout(status, D(stake), D(odds)) == expected


# --- agreed edge cases ---


def test_zero_bets():
    m = compute_metrics([], starting_bankroll=None)
    assert m.n_bets == 0
    assert (m.staked, m.returned, m.profit, m.pending_stake) == (D("0.00"),) * 4
    assert m.roi is None and m.win_rate is None and m.avg_stake is None  # no data != 0%
    assert m.bankroll is None and m.series == []


def test_zero_bets_with_bankroll():
    m = compute_metrics([], starting_bankroll=D("500"))
    assert m.bankroll == D("500.00")
    assert m.available == D("500.00")


def test_all_lost():
    bets = [bet(1, LOST, "10"), bet(2, LOST, "20"), bet(3, LOST, "5.50")]
    m = compute_metrics(bets, starting_bankroll=D("100"))
    assert m.staked == D("35.50")
    assert m.profit == D("-35.50")
    assert m.roi == D("-1.0000")  # -100 %
    assert m.win_rate == D("0.0000")
    assert m.bankroll == D("64.50")
    assert [p.cumulative_profit for p in m.series] == [D("-10.00"), D("-30.00"), D("-35.50")]


def test_void_bets_do_not_count_in_roi():
    with_void = [bet(1, WON, "10", "2.50"), bet(2, LOST, "10"), bet(3, VOID, "1000")]
    without = with_void[:2]
    a = compute_metrics(with_void, None)
    b = compute_metrics(without, None)
    assert (a.roi, a.win_rate, a.avg_stake, a.staked, a.profit) == (
        b.roi, b.win_rate, b.avg_stake, b.staked, b.profit,
    )  # fmt: skip
    assert a.roi == D("0.2500")  # (25 - 20) / 20, checked by hand
    assert a.n_void == 1
    assert [p.bet_id for p in a.series] == [1, 2]  # the void bet is not a point


def test_only_void_bets():
    m = compute_metrics([bet(1, VOID, "10"), bet(2, VOID, "20")], D("50"))
    assert m.roi is None and m.win_rate is None and m.avg_stake is None
    assert m.profit == D("0.00")
    assert m.bankroll == D("50.00")


# --- the rest ---


def test_mixed_portfolio_hand_checked():
    bets = [
        bet(1, WON, "10", "1.95"),  # +9.50
        bet(2, LOST, "20", "2.10"),  # -20.00
        bet(3, WON, "15", "3.40"),  # +36.00
        bet(4, VOID, "10"),
        bet(5, PENDING, "25", "1.80"),
    ]
    m = compute_metrics(bets, starting_bankroll=D("200"))
    assert (m.n_bets, m.n_won, m.n_lost, m.n_void, m.n_pending) == (5, 2, 1, 1, 1)
    assert m.staked == D("45.00")
    assert m.returned == D("70.50")
    assert m.profit == D("25.50")
    assert m.roi == D("0.5667")  # 25.5 / 45 = 0.56666...
    assert m.win_rate == D("0.6667")
    assert m.avg_stake == D("15.00")
    assert m.pending_stake == D("25.00")
    assert m.bankroll == D("225.50")
    assert m.available == D("200.50")  # pending stake is committed money
    assert [(p.bet_id, p.profit, p.bankroll) for p in m.series] == [
        (1, D("9.50"), D("209.50")),
        (2, D("-20.00"), D("189.50")),
        (3, D("36.00"), D("225.50")),
    ]


def test_manual_payout_is_respected():
    """A cash-out or bookmaker-specific settlement: the user's payout wins over odds."""
    m = compute_metrics([bet(1, WON, "10", "3.00", payout="18.20")], None)
    assert m.profit == D("8.20")


def test_series_ordered_by_settlement_not_insertion():
    late = BetRow(1, WON, D("10"), D("20"), T0 + timedelta(days=2))
    early = BetRow(2, LOST, D("10"), D("0"), T0)
    m = compute_metrics([late, early], None)
    assert [p.bet_id for p in m.series] == [2, 1]
    assert m.series[-1].cumulative_profit == D("0.00")


def test_no_floats_anywhere():
    m = compute_metrics([bet(1, WON, "10", "1.95"), bet(2, PENDING, "5")], D("100"))
    for value in (m.staked, m.profit, m.roi, m.win_rate, m.avg_stake, m.bankroll, m.available):
        assert isinstance(value, Decimal)
