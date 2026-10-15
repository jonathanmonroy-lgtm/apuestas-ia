import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import Event, SelfExclusion, SpendingLimit, User
from app.models.enums import LimitPeriod
from app.services.fixtures_provider.football_data import FootballDataProvider
from app.services.http import RawResponse
from app.services.ingestion.fixtures_job import upsert_batch

FIXTURES = Path(__file__).parent / "fixtures"
D = Decimal


@pytest.fixture
def sf(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def client(sf):
    def override_db():
        with sf() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def event_id(sf):
    """A real match (Deportivo - Levante, 2026-10-16) from the recorded calendar."""
    payload = json.loads(
        (FIXTURES / "football_data/matches_pd_2026_real_odds_window.json").read_text("utf-8")
    )
    raw = RawResponse(payload=payload, url="x", http_status=200, headers={}, attempts=1)
    with sf() as s, s.begin():
        upsert_batch(s, FootballDataProvider("k").normalize(raw))
    with sf() as s:
        return s.scalars(select(Event.id).order_by(Event.starts_at)).first()


def new_bet(client, **overrides):
    body = {
        "event_label": "Real Betis - Barça", "selection": "away", "odds_taken": "1.95",
        "stake": "10.00", "currency": "EUR", "placed_at": "2026-10-14T10:00:00Z",
    } | overrides  # fmt: skip
    return client.post("/api/bets", json=body)


def settle(client, bet_id, status, **extra):
    r = client.patch(f"/api/bets/{bet_id}", json={"status": status} | extra)
    assert r.status_code == 200, r.text
    return r.json()


# --- create / validation ---


def test_create_bet_linked_to_a_real_event(client, event_id):
    r = new_bet(client, event_id=event_id, event_label=None)
    assert r.status_code == 201, r.text
    bet = r.json()
    assert bet["event_label"] == "Deportivo - Levante"
    assert bet["event_starts_at"] == "2026-10-16T19:00:00Z"
    assert bet["status"] == "pending"
    assert bet["stake"] == "10.00" and bet["odds_taken"] == "1.95"  # strings, not floats
    assert bet["payout"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"stake": "0"},
        {"stake": "-5"},
        {"stake": "10.001"},  # sub-cent stake
        {"odds_taken": "1.00"},
        {"odds_taken": "1.9555"},
        {"currency": "EURO"},
        {"placed_at": "2026-10-14T10:00:00"},  # naive datetime: refused
        {"event_label": None},  # neither event nor label
        {"event_id": 999999},
        {"selection": "1"},
    ],
)
def test_invalid_bets_are_rejected(client, overrides):
    assert new_bet(client, **overrides).status_code == 422


def test_currency_is_normalized(client):
    assert new_bet(client, currency="eur").json()["currency"] == "EUR"


# --- settle / edit / delete ---


def test_settlement_rules(client):
    won = new_bet(client).json()
    assert settle(client, won["id"], "won")["payout"] == "19.50"  # 10 x 1.95
    lost = new_bet(client, odds_taken="3.40").json()
    body = settle(client, lost["id"], "lost")
    assert (body["payout"], body["profit"]) == ("0.00", "-10.00")
    void = new_bet(client, stake="12.50").json()
    body = settle(client, void["id"], "void")
    assert (body["payout"], body["profit"]) == ("12.50", "0.00")
    assert body["settled_at"] is not None


def test_manual_payout_only_for_won(client):
    bet = new_bet(client).json()
    assert settle(client, bet["id"], "won", payout="15.30")["profit"] == "5.30"  # cash-out
    other = new_bet(client).json()
    r = client.patch(f"/api/bets/{other['id']}", json={"status": "lost", "payout": "3.00"})
    assert r.status_code == 422


def test_settled_bet_must_be_reopened_before_editing(client):
    bet = new_bet(client).json()
    settle(client, bet["id"], "won")
    assert client.patch(f"/api/bets/{bet['id']}", json={"stake": "20"}).status_code == 409
    reopened = settle(client, bet["id"], "pending")
    assert reopened["payout"] is None and reopened["settled_at"] is None
    r = client.patch(f"/api/bets/{bet['id']}", json={"stake": "20.00"})
    assert r.status_code == 200 and r.json()["stake"] == "20.00"
    assert settle(client, bet["id"], "won")["payout"] == "39.00"


def test_notes_editable_any_time_and_delete(client):
    bet = new_bet(client).json()
    settle(client, bet["id"], "lost")
    assert client.patch(f"/api/bets/{bet['id']}", json={"notes": "mala"}).json()["notes"] == "mala"
    assert client.delete(f"/api/bets/{bet['id']}").status_code == 204
    assert client.get(f"/api/bets/{bet['id']}").status_code == 404


def test_list_filters_and_order(client):
    a = new_bet(client, placed_at="2026-10-01T10:00:00Z").json()
    b = new_bet(client, placed_at="2026-10-10T10:00:00Z").json()
    new_bet(client, placed_at="2026-10-12T10:00:00Z", currency="USD")
    settle(client, a["id"], "won")

    body = client.get("/api/bets").json()
    assert body["total"] == 3
    assert [i["placed_at"][:10] for i in body["items"]] == [
        "2026-10-12",
        "2026-10-10",
        "2026-10-01",
    ]
    assert [i["id"] for i in client.get("/api/bets", params={"status": "won"}).json()["items"]] == [
        a["id"]
    ]
    ranged = client.get("/api/bets", params={"date_from": "2026-10-05", "date_to": "2026-10-10"})
    assert [i["id"] for i in ranged.json()["items"]] == [b["id"]]
    assert client.get("/api/bets", params={"currency": "usd"}).json()["total"] == 1


# --- responsible gambling ---


def internal_user_id(client, sf) -> int:
    client.get("/api/bets")  # creates the internal user
    with sf() as s:
        return s.scalar(select(User.id))


def test_active_self_exclusion_blocks_new_bets(client, sf):
    uid = internal_user_id(client, sf)
    with sf() as s, s.begin():
        s.add(SelfExclusion(user_id=uid, ends_at=datetime.now(UTC) + timedelta(days=30)))
    r = new_bet(client)
    assert r.status_code == 403
    assert "autoexclusión" in r.json()["detail"]


def test_expired_self_exclusion_does_not_block(client, sf):
    uid = internal_user_id(client, sf)
    with sf() as s, s.begin():
        s.add(
            SelfExclusion(
                user_id=uid,
                started_at=datetime.now(UTC) - timedelta(days=60),
                ends_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
    assert new_bet(client).status_code == 201


def test_daily_spending_limit(client, sf):
    uid = internal_user_id(client, sf)
    now = datetime.now(UTC)
    with sf() as s, s.begin():
        s.add(SpendingLimit(user_id=uid, period=LimitPeriod.DAILY, amount=D("50"),
                            currency="EUR", effective_from=now - timedelta(days=1)))  # fmt: skip
    today = now.isoformat()
    assert new_bet(client, stake="30.00", placed_at=today).status_code == 201
    voided = new_bet(client, stake="15.00", placed_at=today).json()
    settle(client, voided["id"], "void")  # money given back: doesn't count
    assert new_bet(client, stake="20.00", placed_at=today).status_code == 201  # 50 exactly
    r = new_bet(client, stake="0.01", placed_at=today)
    assert r.status_code == 409
    assert "límite daily de 50" in r.json()["detail"]
    assert new_bet(client, stake="5.00", placed_at=today, currency="USD").status_code == 201


def test_future_limit_not_yet_effective(client, sf):
    uid = internal_user_id(client, sf)
    with sf() as s, s.begin():
        s.add(SpendingLimit(user_id=uid, period=LimitPeriod.DAILY, amount=D("1"), currency="EUR",
                            effective_from=datetime.now(UTC) + timedelta(days=2)))  # fmt: skip
    assert new_bet(client, placed_at=datetime.now(UTC).isoformat()).status_code == 201


# --- metrics end to end ---


def metrics(client, **params):
    r = client.get("/api/bets-metrics", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_metrics_with_zero_bets(client):
    m = metrics(client)
    assert m["n_bets"] == 0
    assert m["roi"] is None and m["win_rate"] is None and m["avg_stake"] is None
    assert m["profit"] == "0.00"
    assert m["series"] == []
    assert m["kind"] == "user_records" and "roi" in m["definitions"]


def test_metrics_all_lost(client):
    for stake in ("10.00", "20.00"):
        settle(client, new_bet(client, stake=stake).json()["id"], "lost")
    m = metrics(client)
    assert (m["roi"], m["win_rate"], m["profit"]) == ("-1.0000", "0.0000", "-30.00")


def test_metrics_voids_excluded_bankroll_and_series(client):
    client.put("/api/me/bankroll", json={"amount": "200.00", "currency": "EUR"})
    w = new_bet(client, stake="10.00", odds_taken="1.95").json()
    lo = new_bet(client, stake="20.00", odds_taken="2.10").json()
    v = new_bet(client, stake="100.00").json()
    new_bet(client, stake="25.00", odds_taken="1.80")  # pending
    settle(client, w["id"], "won", settled_at="2026-10-14T20:00:00Z")
    settle(client, lo["id"], "lost", settled_at="2026-10-14T21:00:00Z")
    settle(client, v["id"], "void", settled_at="2026-10-14T22:00:00Z")

    m = metrics(client)
    assert (m["n_won"], m["n_lost"], m["n_void"], m["n_pending"]) == (1, 1, 1, 1)
    assert m["staked"] == "30.00"  # the 100 void stake is not in
    assert m["profit"] == "-10.50"  # +9.50 - 20.00
    assert m["roi"] == "-0.3500"
    assert m["bankroll"] == "189.50"
    assert m["available"] == "164.50"  # minus the 25 pending
    assert [(p["bet_id"], p["bankroll"]) for p in m["series"]] == [
        (w["id"], "209.50"), (lo["id"], "189.50"),
    ]  # fmt: skip


def test_metrics_never_mix_currencies(client):
    client.put("/api/me/bankroll", json={"amount": "100.00", "currency": "EUR"})
    settle(client, new_bet(client, currency="USD", stake="50.00").json()["id"], "lost")
    eur = metrics(client)
    usd = metrics(client, currency="USD")
    assert eur["n_bets"] == 0 and eur["bankroll"] == "100.00"
    assert usd["profit"] == "-50.00" and usd["bankroll"] is None  # bankroll is in EUR


def test_bankroll_roundtrip(client):
    assert client.get("/api/me/bankroll").json() == {"starting_bankroll": None, "currency": None}
    r = client.put("/api/me/bankroll", json={"amount": "250.5", "currency": "eur"})
    assert r.json() == {"starting_bankroll": "250.5", "currency": "EUR"}
    assert (
        client.put("/api/me/bankroll", json={"amount": "-1", "currency": "EUR"}).status_code == 422
    )
