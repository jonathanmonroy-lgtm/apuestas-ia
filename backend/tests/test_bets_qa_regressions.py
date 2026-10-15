"""Regression tests for the qa-reviewer findings on Phase 5 (bets & responsible gambling)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import SelfExclusion, SpendingLimit, User
from app.models.enums import LimitPeriod
from app.services.responsible_gambling import limits_in_force, set_spending_limit
from tests.test_bets_api import client, internal_user_id, new_bet, settle, sf  # noqa: F401

D = Decimal
NOW = lambda: datetime.now(UTC)  # noqa: E731
YESTERDAY = lambda: (NOW() - timedelta(days=1)).isoformat()  # noqa: E731


def daily_limit(sf, uid, amount="50"):  # noqa: F811
    with sf() as s, s.begin():
        s.add(SpendingLimit(user_id=uid, period=LimitPeriod.DAILY, amount=D(amount),
                            currency="EUR", effective_from=NOW() - timedelta(days=2)))  # fmt: skip


# --- #1: editing a bet goes through the same checks ---


def test_raising_stake_by_patch_respects_the_limit(client, sf):  # noqa: F811
    daily_limit(sf, internal_user_id(client, sf))
    bet = new_bet(client, stake="1.00", placed_at=NOW().isoformat()).json()
    r = client.patch(f"/api/bets/{bet['id']}", json={"stake": "5000.00"})
    assert r.status_code == 409
    assert client.patch(f"/api/bets/{bet['id']}", json={"stake": "50.00"}).status_code == 200
    assert client.patch(f"/api/bets/{bet['id']}", json={"stake": "20.00"}).status_code == 200


def test_raising_stake_while_self_excluded_is_blocked(client, sf):  # noqa: F811
    bet = new_bet(client, stake="10.00").json()
    uid = internal_user_id(client, sf)
    with sf() as s, s.begin():
        s.add(SelfExclusion(user_id=uid, ends_at=None))
    assert client.patch(f"/api/bets/{bet['id']}", json={"stake": "900.00"}).status_code == 403
    # Lowering exposure or recording a result is still allowed while excluded.
    assert client.patch(f"/api/bets/{bet['id']}", json={"stake": "5.00"}).status_code == 200
    assert settle(client, bet["id"], "lost")["status"] == "lost"


def test_reopening_a_void_bet_counts_again(client, sf):  # noqa: F811
    daily_limit(sf, internal_user_id(client, sf))
    big = new_bet(client, stake="40.00").json()
    settle(client, big["id"], "void")
    new_bet(client, stake="30.00")
    r = client.patch(f"/api/bets/{big['id']}", json={"status": "pending"})
    assert r.status_code == 409  # 30 + 40 > 50


# --- #2: back-dating does not escape the limit ---


def test_backdated_bets_count_in_the_period_they_are_logged(client, sf):  # noqa: F811
    daily_limit(sf, internal_user_id(client, sf))
    assert new_bet(client, stake="49.00", placed_at=YESTERDAY()).status_code == 201
    r = new_bet(client, stake="49.00", placed_at=YESTERDAY())
    assert r.status_code == 409


def test_future_dates_are_rejected(client):  # noqa: F811
    future = (NOW() + timedelta(days=1)).isoformat()
    assert new_bet(client, placed_at=future).status_code == 422
    bet = new_bet(client).json()
    r = client.patch(f"/api/bets/{bet['id']}", json={"status": "won", "settled_at": future})
    assert r.status_code == 422


# --- #3: cooling-off never leaves the user without a limit ---


def test_raise_waits_cooling_off_and_old_limit_keeps_applying(sf, client):  # noqa: F811
    uid = internal_user_id(client, sf)
    t0 = datetime(2026, 10, 14, 12, tzinfo=UTC)
    cool = timedelta(hours=24)
    with sf() as s, s.begin():
        set_spending_limit(s, uid, LimitPeriod.DAILY, D("50"), "EUR", t0, cool)
    with sf() as s, s.begin():
        set_spending_limit(
            s, uid, LimitPeriod.DAILY, D("100"), "EUR", t0 + timedelta(hours=1), cool
        )
    with sf() as s:
        during = limits_in_force(s, uid, "EUR", t0 + timedelta(hours=12))
        after = limits_in_force(s, uid, "EUR", t0 + timedelta(hours=26))
    assert [lim.amount for lim in during] == [D("50.00")]  # never zero limits
    assert [lim.amount for lim in after] == [D("100.00")]


def test_lowering_is_immediate(sf, client):  # noqa: F811
    uid = internal_user_id(client, sf)
    t0 = datetime(2026, 10, 14, 12, tzinfo=UTC)
    with sf() as s, s.begin():
        set_spending_limit(s, uid, LimitPeriod.DAILY, D("100"), "EUR", t0, timedelta(hours=24))
        set_spending_limit(
            s,
            uid,
            LimitPeriod.DAILY,
            D("20"),
            "EUR",
            t0 + timedelta(minutes=1),
            timedelta(hours=24),
        )
    with sf() as s:
        now_limits = limits_in_force(s, uid, "EUR", t0 + timedelta(minutes=2))
    assert [lim.amount for lim in now_limits] == [D("20.00")]


# --- #4: limits and self-exclusion are visible and configurable ---


def test_limits_endpoint_shows_used_and_remaining(client):  # noqa: F811
    r = client.put("/api/me/limits", json={"period": "daily", "amount": "50", "currency": "EUR"})
    assert r.status_code == 200
    new_bet(client, stake="12.50")
    body = client.get("/api/me/limits").json()
    (lim,) = body["limits"]
    assert (lim["amount"], lim["used"], lim["remaining"]) == ("50.00", "12.50", "37.50")
    assert body["cooling_off_hours"] == 24

    raised = client.put(
        "/api/me/limits", json={"period": "daily", "amount": "500", "currency": "EUR"}
    )
    (lim,) = raised.json()["limits"]
    assert lim["amount"] == "50.00"  # still the old one...
    assert lim["pending_amount"] == "500.00"  # ...the raise is waiting


def test_self_exclusion_endpoint(client):  # noqa: F811
    assert client.get("/api/me/self-exclusion").json()["active"] is False
    r = client.post("/api/me/self-exclusion", json={"days": 30})
    assert r.status_code == 201
    assert new_bet(client).status_code == 403
    assert client.post("/api/me/self-exclusion", json={"days": 7}).status_code == 409  # shorter
    assert client.post("/api/me/self-exclusion", json={"days": None}).status_code == 201  # longer


# --- #5, #7, #8, #9: validation and normalisation ---


def test_null_status_is_422_not_500(client):  # noqa: F811
    bet = new_bet(client).json()
    assert client.patch(f"/api/bets/{bet['id']}", json={"status": None}).status_code == 422


def test_responses_are_normalised(client):  # noqa: F811
    bet = new_bet(client, stake="10.5", placed_at="2026-10-14T12:00:00+02:00").json()
    assert bet["stake"] == "10.50"
    assert bet["placed_at"] == "2026-10-14T10:00:00Z"
    assert client.get(f"/api/bets/{bet['id']}").json() == bet


def test_settlement_before_placement_is_rejected(client):  # noqa: F811
    bet = new_bet(client, placed_at="2026-10-14T10:00:00Z").json()
    r = client.patch(
        f"/api/bets/{bet['id']}", json={"status": "won", "settled_at": "2026-10-13T10:00:00Z"}
    )
    assert r.status_code == 422


@pytest.mark.parametrize("stake", ["10000000.01", "99999999999.99"])
def test_stake_cap(client, stake):  # noqa: F811
    assert new_bet(client, stake=stake).status_code == 422


def test_max_stake_times_max_odds_fits_the_column(client):  # noqa: F811
    bet = new_bet(client, stake="10000000.00", odds_taken="9999.999").json()
    assert settle(client, bet["id"], "won")["payout"] == "99999990000.00"


def test_internal_user_created_once(client, sf):  # noqa: F811
    for _ in range(3):
        client.get("/api/bets")
    with sf() as s:
        assert len(s.scalars(select(User)).all()) == 1
