"""Multi-country "matches of the day" (API-Football free plan), on real recorded responses
requested EXACTLY as production does (date=<Colombia day>, timezone=America/Bogota):
28 fixtures covering every status seen (FT, PEN, AET, ABD, INT, AWD, PST, 1H, HT, 2H, NS),
and one real odds item (Colombia) that still carries a numeric-valued market (the bug
found on the first real run)."""

import copy
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
from app.models import Competition, Event, EventResult, ExternalRef, OddSnapshot
from app.models.enums import (
    EntityType,
    EventStatus,
    IngestionStatus,
    Market,
    Provider,
    QuotaPeriod,
    Selection,
)
from app.services.fixtures_provider.api_football import ApiFootballProvider, country_code_from_flag
from app.services.http import RawResponse
from app.services.ingestion.day_job import run_day_fixtures, run_event_odds, store_fixture_odds
from app.services.quota import QuotaPolicy

FIXTURES = Path(__file__).parent / "fixtures/api_football"
POLICY = QuotaPolicy(QuotaPeriod.DAY, 90)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def raw(payload, url="https://x") -> RawResponse:
    return RawResponse(payload=payload, url=url, http_status=200, headers={}, attempts=1)


DAY = load("fixtures_day_colombia_real.json")
ODDS = load("odds_day_2026-10-15_real.json")
ODDS_FIXTURE = str(ODDS["response"][0]["fixture"]["id"])  # a Colombian match, 2026-10-15 14:00


def items_with(status: str) -> list[dict]:
    return [it for it in DAY["response"] if it["fixture"]["status"]["short"] == status]


class FakeApiFootball(ApiFootballProvider):
    def __init__(self, day=None, odds=None):
        self.day_payload = day if day is not None else DAY
        self.odds_payload = odds if odds is not None else ODDS
        self.requested: list[str] = []

    def fetch_day(self, day):
        self.requested.append(f"day={day}")
        return raw(self.day_payload)

    def fetch_fixture_odds(self, fixture_external_id):
        self.requested.append(f"fixture={fixture_external_id}")
        # Only the recorded fixture has odds; any other fixture gets the real empty shape.
        payload = self.odds_payload if fixture_external_id == ODDS_FIXTURE else {
            "get": "odds", "errors": [], "results": 0, "response": [],
        }  # fmt: skip
        return raw(
            payload, url=f"https://v3.football.api-sports.io/odds?fixture={fixture_external_id}"
        )

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


def event_id_of(sf, fixture_id: str) -> int:
    with sf() as s:
        return s.scalar(
            select(ExternalRef.entity_id).where(
                ExternalRef.source == Provider.API_FOOTBALL,
                ExternalRef.entity_type == EntityType.EVENT,
                ExternalRef.external_id == fixture_id,
            )
        )


# --- normalizer ---


def test_production_shaped_payload():
    assert DAY["parameters"]["timezone"] == "America/Bogota"
    assert all(it["fixture"]["date"].endswith("-05:00") for it in DAY["response"])


def test_country_codes_from_flags():
    assert country_code_from_flag("https://media.api-sports.io/flags/co.svg") == "CO"
    assert country_code_from_flag("https://media.api-sports.io/flags/gb-eng.svg") is None
    assert country_code_from_flag(None) is None


def test_normalize_day_groups_by_league_with_country():
    batches = ApiFootballProvider("k").normalize_day(raw(DAY))
    assert sum(len(b.fixtures) for b in batches) == 28
    countries = {b.competition.country_name: b.competition.country_code for b in batches}
    assert countries["Colombia"] == "CO"
    assert countries["England"] is None  # no ISO alpha-2 for a UK nation
    assert countries["World"] is None
    # Colombian kick-offs are parsed as aware datetimes and compare in UTC.
    colombian = next(f for b in batches for f in b.fixtures if f.external_id == ODDS_FIXTURE)
    assert colombian.starts_at == datetime(2026, 10, 15, 19, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "expected", "confirmed", "has_result"),
    [
        ("FT", EventStatus.FINISHED, True, True),
        ("PEN", EventStatus.FINISHED, True, True),  # 90-minute score, penalties apart
        ("AET", EventStatus.FINISHED, True, True),
        ("AWD", EventStatus.FINISHED, True, False),  # administrative: no played score
        ("INT", EventStatus.LIVE, True, False),  # interrupted mid-match, not postponed
        ("PST", EventStatus.POSTPONED, False, False),
        ("ABD", EventStatus.CANCELLED, False, False),
        ("2H", EventStatus.LIVE, True, False),
        ("NS", EventStatus.SCHEDULED, True, False),
    ],
)
def test_status_mapping_on_real_items(status, expected, confirmed, has_result):
    item = items_with(status)[0]
    (batch,) = ApiFootballProvider("k").normalize_day(raw({"response": [item]}))
    (f,) = batch.fixtures
    assert (f.status, f.kickoff_confirmed, f.full_time is not None) == (
        expected, confirmed, has_result,
    )  # fmt: skip


def test_tbd_is_a_date_only_kickoff():
    """None of the recorded days had a TBD match: one real NS item, status changed."""
    item = copy.deepcopy(items_with("NS")[0])
    item["fixture"]["status"]["short"] = "TBD"
    (batch,) = ApiFootballProvider("k").normalize_day(raw({"response": [item]}))
    assert batch.fixtures[0].kickoff_confirmed is False


def test_odds_parse_only_match_winner_and_survive_numeric_markets():
    (fo,) = ApiFootballProvider("k").normalize_odds(raw(ODDS))
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
    assert ingested.records_written == 28
    with sf() as s:
        names = set(s.scalars(select(Competition.country_name)).all())
        assert {"Colombia", "England", "World", "Brazil", "Argentina", "Spain"} <= names
        played = sum(len(items_with(st)) for st in ("FT", "PEN", "AET"))
        assert s.scalar(select(func.count()).select_from(EventResult)) == played


def test_rerun_is_idempotent(sf, ingested):
    run_day_fixtures(sf, FakeApiFootball(), date(2026, 10, 15), policy=POLICY)
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Event)) == 28


def test_default_settings_skip_laliga():
    """LaLiga (API-Football league 140) comes from football-data: never ingested twice."""
    assert "140" in Settings().api_football_skip_leagues


def test_skipped_leagues_are_not_stored(sf):
    skipped = {
        str(it["league"]["id"]) for it in DAY["response"] if it["league"]["country"] == "Spain"
    }
    run_day_fixtures(sf, FakeApiFootball(), date(2026, 10, 15), policy=POLICY,
                     skip_league_ids=frozenset(skipped))  # fmt: skip
    with sf() as s:
        assert "Spain" not in set(s.scalars(select(Competition.country_name)).all())


def test_odds_attach_to_known_events_only(sf, ingested):
    odds = ApiFootballProvider("k").normalize_odds(raw(ODDS))
    with sf() as s, s.begin():
        assert store_fixture_odds(s, odds, None) == len(odds[0].prices)
    orphan = odds[0].model_copy(update={"fixture_external_id": "999999999"})
    with sf() as s, s.begin():
        assert store_fixture_odds(s, [orphan], None) == 0  # never creates an event
        assert s.scalar(select(func.count()).select_from(Event)) == 28


def test_on_demand_requests_the_right_match(sf, ingested):
    provider = FakeApiFootball()
    outcome = run_event_odds(sf, provider, event_id_of(sf, ODDS_FIXTURE), policy=POLICY)
    assert outcome.status is IngestionStatus.SUCCESS and outcome.records_written > 0
    assert provider.requested == [f"fixture={ODDS_FIXTURE}"]


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

CLOCK = datetime(2026, 10, 15, 12, tzinfo=UTC)  # 07:00 in Colombia, before the 14:00 match


@pytest.fixture
def client(sf):
    def override_db():
        with sf() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings(api_football_key="k")
    app.dependency_overrides[utc_now] = lambda: CLOCK
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def fake(monkeypatch):
    provider = FakeApiFootball()
    monkeypatch.setattr(events_api, "api_football", lambda settings: provider)
    return provider


def test_countries_menu_for_colombias_day(client, ingested):
    body = client.get("/api/countries", params={"day": "2026-10-15"}).json()
    assert body["timezone"] == "UTC-5"
    names = [c["name"] for c in body["countries"]]
    assert names[-1] == "World" and names[:-1] == sorted(names[:-1])
    start, end = local_day_bounds(date(2026, 10, 15), -5)
    in_day = sum(1 for it in DAY["response"]
                 if start <= datetime.fromisoformat(it["fixture"]["date"]) < end)  # fmt: skip
    assert sum(c["n_events"] for c in body["countries"]) == in_day


def test_events_of_one_country(client, ingested):
    events = client.get("/api/events", params={"day": "2026-10-15", "country": "Colombia"}).json()
    assert events["events"] and all(e["country"] == "Colombia" for e in events["events"])


def test_world_includes_competitions_without_country(client, sf, ingested):
    with sf() as s, s.begin():
        comp = s.scalars(select(Competition).where(Competition.country_name == "World")).first()
        comp.country_name = None
    body = client.get("/api/countries", params={"day": "2026-10-15"}).json()
    world = next(c for c in body["countries"] if c["name"] == "World")
    events = client.get("/api/events", params={"day": "2026-10-15", "country": "World"}).json()
    assert len(events["events"]) == world["n_events"] > 0


def test_fetch_odds_then_cooldown(client, sf, ingested, fake):
    eid = event_id_of(sf, ODDS_FIXTURE)
    first = client.post(f"/api/events/{eid}/fetch-odds").json()
    assert first["status"] == "ok" and first["snapshots_inserted"] > 0
    again = client.post(f"/api/events/{eid}/fetch-odds").json()
    assert again["status"] == "recent"  # QA #1: answered from the database, no request
    assert fake.requested == [f"fixture={ODDS_FIXTURE}"]


def test_empty_answer_also_cools_down(client, sf, ingested, fake):
    other = next(it for it in DAY["response"]
                 if it["fixture"]["status"]["short"] == "NS"
                 and str(it["fixture"]["id"]) != ODDS_FIXTURE
                 and datetime.fromisoformat(it["fixture"]["date"]) > CLOCK)  # fmt: skip
    eid = event_id_of(sf, str(other["fixture"]["id"]))
    assert client.post(f"/api/events/{eid}/fetch-odds").json()["snapshots_inserted"] == 0
    assert client.post(f"/api/events/{eid}/fetch-odds").json()["status"] == "recent"
    assert len(fake.requested) == 1


def test_on_demand_cap_is_separate(client, sf, ingested, fake):
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_football_key="k", api_football_on_demand_daily_cap=0
    )
    r = client.post(f"/api/events/{event_id_of(sf, ODDS_FIXTURE)}/fetch-odds").json()
    assert r["status"] == "no_budget"
    assert fake.requested == []


def test_fetch_odds_refuses_started_matches(client, sf, ingested, fake):
    with sf() as s:
        finished = s.scalars(select(Event).where(Event.status == EventStatus.FINISHED)).first()
    assert client.post(f"/api/events/{finished.id}/fetch-odds").status_code == 409
    assert client.post("/api/events/999999/fetch-odds").status_code == 404


# --- odds summary: one source per match, exchange excluded, counts match the page ---


def add_quotes(sf, eid, source, book, prices, at=datetime(2026, 10, 15, 2, tzinfo=UTC)):
    with sf() as s, s.begin():
        for sel, p in zip((Selection.HOME, Selection.DRAW, Selection.AWAY), prices, strict=True):
            s.add(OddSnapshot(event_id=eid, source=source, bookmaker=book, market=Market.H2H,
                              selection=sel, price=Decimal(p), captured_at=at))  # fmt: skip


def test_preferred_source_wins_and_sources_never_mix(client, sf, ingested):
    eid = event_id_of(sf, ODDS_FIXTURE)
    add_quotes(sf, eid, Provider.API_FOOTBALL, "Bet365", ("2.00", "3.00", "4.00"))
    add_quotes(sf, eid, Provider.THE_ODDS_API, "pinnacle", ("9.00", "9.00", "1.10"))
    ev = next(e for e in client.get("/api/events", params={"day": "2026-10-15"}).json()["events"]
              if e["id"] == eid)  # fmt: skip
    assert ev["odds"]["source"] == "the_odds_api"
    assert ev["odds"]["n_bookmakers"] == 1
    assert ev["odds"]["median_price"]["home"] == "9"  # same string on SQLite and Postgres


def test_betfair_only_match_shows_no_odds_and_is_not_counted(client, sf, ingested):
    eid = event_id_of(sf, ODDS_FIXTURE)
    add_quotes(sf, eid, Provider.API_FOOTBALL, "Betfair", ("2.00", "3.00", "4.00"))
    ev = next(e for e in client.get("/api/events", params={"day": "2026-10-15"}).json()["events"]
              if e["id"] == eid)  # fmt: skip
    assert ev["odds"] is None  # the exchange is excluded from the median
    colombia = next(c for c in client.get("/api/countries", params={"day": "2026-10-15"}).json()
                    ["countries"] if c["name"] == "Colombia")  # fmt: skip
    assert colombia["n_with_odds"] == 0  # QA #5: the count matches what the page shows


def test_api_football_method_text_is_honest(client, sf, ingested):
    eid = event_id_of(sf, ODDS_FIXTURE)
    add_quotes(sf, eid, Provider.API_FOOTBALL, "Bet365", ("2.00", "3.00", "4.00"))
    ev = next(e for e in client.get("/api/events", params={"day": "2026-10-15"}).json()["events"]
              if e["id"] == eid)  # fmt: skip
    assert "una sola hora de actualización por partido" in ev["odds"]["method"]


def test_tbd_match_keeps_prematch_odds_all_day(client, sf, ingested):
    """QA #8: a date-only kick-off (stored at 00:00 local) is pre-match the whole day."""
    eid = event_id_of(sf, ODDS_FIXTURE)
    with sf() as s, s.begin():
        e = s.get(Event, eid)
        e.kickoff_confirmed = False
        e.starts_at = datetime(2026, 10, 15, 5, tzinfo=UTC)  # 00:00 in Colombia
    add_quotes(sf, eid, Provider.API_FOOTBALL, "Bet365", ("2.00", "3.00", "4.00"),
               at=datetime(2026, 10, 15, 10, tzinfo=UTC))  # fmt: skip
    ev = next(e for e in client.get("/api/events", params={"day": "2026-10-15"}).json()["events"]
              if e["id"] == eid)  # fmt: skip
    assert ev["odds"] is not None
    assert ev["can_fetch_odds"] is True
