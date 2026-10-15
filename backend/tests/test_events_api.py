from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.events import utc_now
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
    # QA F3 #3: the median is a statistic, labelled as analysis with its method
    assert dep["odds"]["kind"] == "analysis"
    assert "Mediana" in dep["odds"]["method"]
    assert dep["odds"]["n_bookmakers"] == 3  # the exchange is excluded
    assert {k: Decimal(v) for k, v in dep["odds"]["median_price"].items()} == {
        "home": Decimal("2.05"), "draw": Decimal("3.3"), "away": Decimal("3.3"),
    }  # fmt: skip
    assert dep["odds"]["as_of"] == "2026-10-14T23:05:01Z"  # pinnacle, freshest bookmaker
    ip = dep["implied_probability"]
    assert ip["kind"] == "analysis"
    # Checked with exact fractions: 0.445946, 0.277027, 0.277027; margin 9.39%
    assert (ip["home"], ip["draw"], ip["away"], ip["overround"]) == (
        "0.4459", "0.2770", "0.2770", "0.0939",
    )  # fmt: skip


def test_internal_mode_never_exposes_bookmakers(client, with_odds):
    text = client.get(
        "/api/events", params={"date_from": "2026-10-16", "date_to": "2026-10-26"}
    ).text.lower()
    for name in ("pinnacle", "williamhill", "unibet", "betfair", 'bookmaker"'):
        assert name not in text


def at(when: datetime) -> None:
    app.dependency_overrides[utc_now] = lambda: when


def test_old_odds_are_flagged_stale(client, with_odds):
    at(datetime(2026, 10, 15, 6, 0, tzinfo=UTC))  # 7h after the recorded quotes
    assert get(client)["events"][0]["odds"]["is_stale"] is True


def test_fresh_odds_are_not_stale(client, with_odds):
    at(datetime(2026, 10, 14, 23, 30, tzinfo=UTC))  # 25 min after
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


def test_matchday_returns_a_whole_round_past_or_future(client, sf):
    """The schedule view must reach every match of the season, not only the next days."""
    import json
    from pathlib import Path

    from app.services.fixtures_provider.football_data import FootballDataProvider
    from app.services.http import RawResponse
    from app.services.ingestion.fixtures_job import upsert_batch

    payload = json.loads(
        (
            Path(__file__).parent / "fixtures/football_data/matches_pd_2026_real_matchday1.json"
        ).read_text(encoding="utf-8")
    )
    raw = RawResponse(payload=payload, url="x", http_status=200, headers={}, attempts=1)
    with sf() as s, s.begin():
        upsert_batch(s, FootballDataProvider("k").normalize(raw))

    body = client.get("/api/events", params={"matchday": 1}).json()
    assert len(body["events"]) == 10  # all 10 matches of matchday 1 (Aug 2026, played)
    assert all(e["round"] == "Jornada 1" for e in body["events"])
    assert all(e["result"] is not None for e in body["events"])
    assert client.get("/api/events", params={"matchday": 2}).json()["events"] == []
    assert client.get("/api/events", params={"matchday": 0}).status_code == 422
