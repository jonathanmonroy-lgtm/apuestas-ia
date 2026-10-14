from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models.enums import QuotaPeriod
from app.services.ingestion.odds_job import run_odds_ingestion
from app.services.quota import QuotaPolicy
from tests.test_odds_job import ODDS, FakeOddsProvider, calendar, load  # noqa: F401


@pytest.fixture
def sf(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def client(sf):
    def override_db():
        with sf() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(odds_stale_after_hours=4)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def with_odds(sf, calendar):  # noqa: F811
    run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=QuotaPolicy(QuotaPeriod.MONTH, 450))


def get(client, **params):
    response = client.get(
        "/api/events", params={"date_from": "2026-10-16", "date_to": "2026-10-26", **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_events_with_odds_and_implied_probability(client, with_odds):
    body = get(client)

    assert body["data_as_of"]["odds"] is not None
    assert [e["home"]["name"] for e in body["events"]] == ["Deportivo", "Real Betis", "Barça"]
    dep = body["events"][0]
    assert dep["starts_at"] == "2026-10-16T19:00:00Z"
    assert dep["kickoff_confirmed"] is True
    assert dep["odds"]["kind"] == "data"
    assert dep["odds"]["n_bookmakers"] == 4
    assert {k: Decimal(v) for k, v in dep["odds"]["median_price"].items()} == {
        "home": Decimal("2.085"), "draw": Decimal("3.41"), "away": Decimal("3.455"),
    }  # fmt: skip
    assert dep["odds"]["as_of"] == "2026-10-14T23:05:05Z"
    ip = dep["implied_probability"]
    assert ip["kind"] == "analysis"
    assert (ip["home"], ip["draw"], ip["away"]) == ("0.4515", "0.2761", "0.2725")


def test_internal_mode_never_exposes_bookmakers(client, with_odds):
    text = client.get(
        "/api/events", params={"date_from": "2026-10-16", "date_to": "2026-10-26"}
    ).text.lower()
    for name in ("pinnacle", "williamhill", "unibet", "betfair", 'bookmaker"'):
        assert name not in text


def odds_observed_at(sf, when: datetime) -> None:
    """Real recorded prices, re-stamped so staleness doesn't depend on the test's date."""
    payload = load(ODDS)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    for e in payload:
        for b in e["bookmakers"]:
            b["last_update"] = stamp
            for m in b["markets"]:
                m["last_update"] = stamp
    run_odds_ingestion(
        sf, FakeOddsProvider(payload), "PD", policy=QuotaPolicy(QuotaPeriod.MONTH, 450)
    )


def test_old_odds_are_flagged_stale(client, sf, calendar):  # noqa: F811
    odds_observed_at(sf, datetime.now(UTC) - timedelta(hours=5))
    assert get(client)["events"][0]["odds"]["is_stale"] is True


def test_fresh_odds_are_not_stale(client, sf, calendar):  # noqa: F811
    odds_observed_at(sf, datetime.now(UTC) - timedelta(minutes=10))
    assert get(client)["events"][0]["odds"]["is_stale"] is False


def test_event_without_odds_has_nulls_not_invented_values(client, calendar):  # noqa: F811
    body = get(client)
    assert body["data_as_of"]["odds"] is None
    assert all(e["odds"] is None and e["implied_probability"] is None for e in body["events"])


def test_date_range_validation(client):
    assert (
        client.get(
            "/api/events", params={"date_from": "2026-10-10", "date_to": "2026-10-01"}
        ).status_code
        == 422
    )
    too_long = (datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=200)).date().isoformat()
    assert (
        client.get(
            "/api/events", params={"date_from": "2026-10-01", "date_to": too_long}
        ).status_code
        == 422
    )
