"""Multi-country "matches of the day" (API-Football free plan), on real recorded responses:
14 fixtures of 2026-10-15 covering every status seen that day, and one real odds item
(Colombia) that still carries a numeric-valued market (the bug found on the first run)."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import app.api.events as events_api
from app.api.events import local_day_bounds, utc_now
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.jobs.sync_day import days_in_free_window
from app.main import app
from app.models import Competition, Event, EventResult, OddSnapshot
from app.models.enums import EventStatus, IngestionStatus, Provider, QuotaPeriod, Selection
from app.services.fixtures_provider.api_football import ApiFootballProvider, country_code_from_flag
from app.services.http import RawResponse
from app.services.ingestion.day_job import run_day_fixtures, run_event_odds, store_fixture_odds
from app.services.quota import QuotaPolicy

FIXTURES = Path(__file__).parent / "fixtures/api_football"
POLICY = QuotaPolicy(QuotaPeriod.DAY, 90)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def raw(payload) -> RawResponse:
    return RawResponse(payload=payload, url="https://x", http_status=200, headers={}, attempts=1)


DAY = load("fixtures_day_2026-10-15_real.json")
ODDS = load("odds_day_2026-10-15_real.json")


class FakeApiFootball(ApiFootballProvider):
    def __init__(self, day=None, odds=None):
        self.day_payload = day if day is not None else DAY
        self.odds_payload = odds if odds is not None else ODDS
        self.calls = 0

    def fetch_day(self, day):
        self.calls += 1
        return raw(self.day_payload)

    def fetch_fixture_odds(self, fixture_external_id):
        self.calls += 1
        return raw(self.odds_payload)

    def close(self):
        pass


@pytest.fixture
def sf(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def ingested(sf):
    outcome = run_day_fixtures(sf, FakeApiFootball(), date(2026, 10, 15), policy=POLICY)
    assert outcome.status is IngestionStatus.SUCCESS
    return outcome


# --- normalizer ---


def test_country_codes_from_flags():
    assert country_code_from_flag("https://media.api-sports.io/flags/co.svg") == "CO"
    assert country_code_from_flag("https://media.api-sports.io/flags/gb-eng.svg") is None
    assert country_code_from_flag(None) is None


def test_normalize_day_groups_by_league_with_country():
    batches = ApiFootballProvider("k").normalize_day(raw(DAY))
    assert sum(len(b.fixtures) for b in batches) == 14
    countries = {b.competition.country_name: b.competition.country_code for b in batches}
    assert countries["Colombia"] == "CO"
    assert countries["England"] is None  # no ISO alpha-2 for a UK nation
    assert countries["World"] is None


def test_status_mapping_and_results_only_when_played():
    fixtures = {
        f.external_id: f
        for b in ApiFootballProvider("k").normalize_day(raw(DAY))
        for f in b.fixtures
    }
    by_status = {
        it["fixture"]["status"]["short"]: str(it["fixture"]["id"]) for it in DAY["response"]
    }
    ft = fixtures[by_status["FT"]]
    assert ft.status is EventStatus.FINISHED and ft.full_time is not None
    live = fixtures[by_status["2H"]]
    assert live.status is EventStatus.LIVE and live.full_time is None  # no score mid-match
    pst = fixtures[by_status["PST"]]
    assert pst.status is EventStatus.POSTPONED and pst.kickoff_confirmed is False
    awd = fixtures[by_status["AWD"]]
    assert awd.status is EventStatus.FINISHED and awd.full_time is None  # administrative


def test_odds_parse_only_match_winner_and_survive_numeric_markets():
    (fo,) = ApiFootballProvider("k").normalize_odds(raw(ODDS))
    assert fo.prices
    assert {p.selection for p in fo.prices} == {Selection.HOME, Selection.DRAW, Selection.AWAY}
    assert all(isinstance(p.price, Decimal) and p.price > 1 for p in fo.prices)
    item = ODDS["response"][0]
    assert any(  # the real payload still holds the market that broke the first version
        isinstance(v["value"], int)
        for b in item["bookmakers"]
        for x in b["bets"]
        for v in x["values"]
    )


# --- ingestion ---


def test_day_ingestion_stores_every_country(sf, ingested):
    assert ingested.records_written == 14
    with sf() as s:
        names = set(s.scalars(select(Competition.country_name)).all())
        assert {"Colombia", "England", "World", "Brazil", "Argentina"} <= names
        assert s.scalar(select(func.count()).select_from(EventResult)) == 2  # the two FT


def test_rerun_is_idempotent(sf, ingested):
    run_day_fixtures(sf, FakeApiFootball(), date(2026, 10, 15), policy=POLICY)
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Event)) == 14


def test_skipped_leagues_never_duplicate_another_source(sf):
    brazil = next(
        it["league"]["id"] for it in DAY["response"] if it["league"]["country"] == "Brazil"
    )
    run_day_fixtures(sf, FakeApiFootball(), date(2026, 10, 15), policy=POLICY,
                     skip_league_ids=frozenset({str(brazil)}))  # fmt: skip
    with sf() as s:
        assert "Brazil" not in set(s.scalars(select(Competition.country_name)).all())


def test_odds_attach_to_known_events_only(sf, ingested):
    odds = ApiFootballProvider("k").normalize_odds(raw(ODDS))
    with sf() as s, s.begin():
        inserted = store_fixture_odds(s, odds, None)
    assert inserted == len(odds[0].prices)
    orphan = odds[0].model_copy(update={"fixture_external_id": "999999999"})
    with sf() as s, s.begin():
        assert store_fixture_odds(s, [orphan], None) == 0  # never creates an event
        assert s.scalar(select(func.count()).select_from(Event)) == 14


def test_on_demand_odds_for_one_match(sf, ingested):
    fixture_id = str(ODDS["response"][0]["fixture"]["id"])
    with sf() as s:
        from app.models import ExternalRef
        from app.models.enums import EntityType

        event_id = s.scalar(
            select(ExternalRef.entity_id).where(
                ExternalRef.source == Provider.API_FOOTBALL,
                ExternalRef.entity_type == EntityType.EVENT,
                ExternalRef.external_id == fixture_id,
            )
        )
    provider = FakeApiFootball()
    outcome = run_event_odds(sf, provider, event_id, policy=POLICY)
    assert outcome.status is IngestionStatus.SUCCESS and outcome.records_written > 0
    assert provider.calls == 1


# --- free-plan date window and Colombia's day ---


def test_free_window_is_utc_yesterday_to_tomorrow():
    now = datetime(2026, 10, 15, 3, 13, tzinfo=UTC)  # 22:13 on the 14th in Colombia
    days = [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)]
    assert days_in_free_window(days, now) == [date(2026, 10, 14), date(2026, 10, 15)]


def test_colombian_day_bounds():
    assert local_day_bounds(date(2026, 10, 15), -5) == (
        datetime(2026, 10, 15, 5, tzinfo=UTC),
        datetime(2026, 10, 16, 5, tzinfo=UTC),
    )


# --- API ---


@pytest.fixture
def client(sf):
    def override_db():
        with sf() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(api_football_key="k")
    app.dependency_overrides[utc_now] = lambda: datetime(2026, 10, 15, 12, tzinfo=UTC)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_countries_menu_for_colombias_day(client, ingested):
    body = client.get("/api/countries", params={"day": "2026-10-15"}).json()
    assert body["timezone"] == "UTC-5"
    names = [c["name"] for c in body["countries"]]
    assert names[-1] == "World"  # international competitions go last
    assert names[:-1] == sorted(names[:-1])
    total = sum(c["n_events"] for c in body["countries"])
    # Only matches whose kick-off falls in Colombia's 2026-10-15 count.
    start, end = local_day_bounds(date(2026, 10, 15), -5)
    in_day = sum(
        1 for it in DAY["response"]
        if start <= datetime.fromisoformat(it["fixture"]["date"]) < end
    )  # fmt: skip
    assert total == in_day


def test_events_of_one_country(client, ingested):
    events = client.get("/api/events", params={"day": "2026-10-15", "country": "Colombia"}).json()
    assert events["events"]
    assert all(e["country"] == "Colombia" for e in events["events"])


def test_fetch_odds_endpoint(client, sf, ingested, monkeypatch):
    monkeypatch.setattr(events_api, "api_football", lambda settings: FakeApiFootball())
    fixture_id = str(ODDS["response"][0]["fixture"]["id"])
    with sf() as s:
        from app.models import ExternalRef
        from app.models.enums import EntityType

        event = s.get(
            Event,
            s.scalar(
                select(ExternalRef.entity_id).where(
                    ExternalRef.source == Provider.API_FOOTBALL,
                    ExternalRef.entity_type == EntityType.EVENT,
                    ExternalRef.external_id == fixture_id,
                )
            ),
        )
    if event.starts_at <= datetime(2026, 10, 15, 12, tzinfo=UTC):
        pytest.skip("recorded odds item is for a match already started at the test clock")
    r = client.post(f"/api/events/{event.id}/fetch-odds")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(OddSnapshot)) > 0
    assert client.post("/api/events/999999/fetch-odds").status_code == 404


def test_fetch_odds_refuses_started_matches(client, sf, ingested, monkeypatch):
    monkeypatch.setattr(events_api, "api_football", lambda settings: FakeApiFootball())
    with sf() as s:
        finished = s.scalars(select(Event).where(Event.status == EventStatus.FINISHED)).first()
    assert client.post(f"/api/events/{finished.id}/fetch-odds").status_code == 409
