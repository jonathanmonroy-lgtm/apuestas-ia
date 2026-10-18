"""ESPN as the multi-country source, on REAL responses recorded 2026-10-17 (a subset of
the 429 matches of /soccer/all/scoreboard, heavy unused fields removed): Colombia, MLS
matches with DraftKings lines before kick-off, live matches that also carry lines (must
NOT be stored: pre-match only), a penalty shoot-out, a postponed match, LaLiga (owned by
football-data: skipped) and an international tournament."""

import copy
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.events import utc_now
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.jobs.sync_espn import espn_days, sync_espn
from app.jobs.sync_fixtures import EXIT_FAILED, EXIT_OK
from app.main import app
from app.models import Competition, Event, EventResult, ExternalRef, IngestionRun, OddSnapshot
from app.models.enums import (
    EntityType,
    EventStatus,
    IngestionStatus,
    Provider,
    QuotaPeriod,
    Selection,
)
from app.services.errors import FormatError, ProviderUnavailableError
from app.services.fixtures_provider.espn import (
    EspnProvider,
    american_to_decimal,
    country_of,
)
from app.services.http import RawResponse
from app.services.ingestion.espn_job import run_espn_day, run_espn_leagues
from app.services.quota import QuotaPolicy

FIXTURES = Path(__file__).parent / "fixtures/espn"
POLICY = QuotaPolicy(QuotaPeriod.DAY, 400)
SEEN_AT = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)  # when "we" read the scoreboard
REFRESH = timedelta(hours=2)
DAY = date(2026, 10, 17)


def cfg(**overrides) -> Settings:
    """Settings without the developer's .env (QA: results must not depend on the machine)."""
    return Settings(_env_file=None, **overrides)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


SCOREBOARD = load("scoreboard_all_2026-10-17_real.json")
LEAGUES = load("leagues_dropdown_real.json")


def raw(payload, at: datetime = SEEN_AT) -> RawResponse:
    return RawResponse(
        payload=payload, url="https://site.api.espn.com/x", http_status=200, headers={},
        attempts=1, fetched_at=at,
    )  # fmt: skip


def leagues():
    return EspnProvider.normalize_leagues(raw(LEAGUES))


def event(espn_id: str) -> dict:
    return next(e for e in SCOREBOARD["events"] if e["id"] == espn_id)


class FakeEspn(EspnProvider):
    def __init__(self, scoreboard=None, at: datetime = SEEN_AT, fail_leagues=False):
        self.scoreboard = scoreboard if scoreboard is not None else SCOREBOARD
        self.at = at
        self.fail_leagues = fail_leagues
        self.requested: list[str] = []

    def fetch_leagues(self):
        self.requested.append("leagues")
        if self.fail_leagues:
            raise ProviderUnavailableError("HTTP 503", http_status=503)
        return raw(LEAGUES, self.at)

    def fetch_scoreboard(self, day):
        self.requested.append(day.isoformat())
        return raw(self.scoreboard, self.at)

    def close(self):
        pass


@pytest.fixture
def sf(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


def ingest(sf, provider=None, skip=frozenset({"esp.1"})):
    provider = provider or FakeEspn()
    outcome = run_espn_day(
        sf, provider, DAY, leagues(), policy=POLICY, odds_refresh=REFRESH,
        skip_league_slugs=skip,
    )  # fmt: skip
    assert outcome.status is IngestionStatus.SUCCESS
    return outcome


def event_id(sf, espn_id: str) -> int:
    with sf() as s:
        return s.scalar(
            select(ExternalRef.entity_id).where(
                ExternalRef.source == Provider.ESPN,
                ExternalRef.entity_type == EntityType.EVENT,
                ExternalRef.external_id == espn_id,
            )
        )


def n_snapshots(sf) -> int:
    with sf() as s:
        return s.scalar(select(func.count()).select_from(OddSnapshot))


# --- pure helpers ---


@pytest.mark.parametrize(
    ("american", "decimal"),
    [
        ("+230", "3.300"),
        ("+100", "2.000"),
        ("-115", "1.870"),  # 1 + 100/115 = 1.8695... rounded to the column's 3 decimals
        ("-700", "1.143"),
        ("+1300", "14.000"),
        (260, "3.600"),  # drawOdds.moneyLine comes as a number
        ("EVEN", "2.000"),
    ],
)
def test_american_to_decimal(american, decimal):
    assert american_to_decimal(american) == Decimal(decimal)


@pytest.mark.parametrize("bad", [None, "", "OFF", "+50", "-99", "0", "NaN", "Infinity", "-inf"])
def test_american_to_decimal_refuses_what_is_not_a_price(bad):
    assert american_to_decimal(bad) is None


def test_country_from_league_slug():
    assert country_of("col.1") == ("Colombia", "CO")
    assert country_of("eng.w.league_cup") == ("England", None)  # no ISO alpha-2
    assert country_of("usa.usl.l1") == ("USA", "US")
    assert country_of("fifa.wworld.u17") == ("World", None)
    assert country_of("uefa.champions") == ("World", None)
    assert country_of("xyz.1") == ("World", None)  # unknown: logged, never invented
    assert country_of(None) == ("World", None)


# --- normalizer ---


def test_league_catalogue_skips_the_real_entry_without_id():
    by_id = leagues()
    assert by_id["650"].slug == "col.1"
    assert all(lg.id and lg.slug for lg in by_id.values())
    assert len(by_id) == len(LEAGUES["leagues"]) - 1


def test_normalize_groups_by_league_with_country():
    batches, _ = EspnProvider().normalize_scoreboard(raw(SCOREBOARD), leagues())
    assert sum(len(b.fixtures) for b in batches) == 22
    by_slug = {b.competition.external_id: b.competition for b in batches}
    assert (by_slug["col.1"].country_name, by_slug["col.1"].country_code) == ("Colombia", "CO")
    assert by_slug["col.1"].name == "Colombian Primera A"
    assert by_slug["eng.1"].country_name == "England"
    assert by_slug["fifa.wworld.u17"].country_name == "World"


def test_statuses_scores_and_rounds():
    batches, _ = EspnProvider().normalize_scoreboard(raw(SCOREBOARD), leagues())
    f = {fx.external_id: fx for b in batches for fx in b.fixtures}
    nacional = f["401877851"]  # Boyacá Chicó 1-4 Atlético Nacional, full time
    assert nacional.status is EventStatus.FINISHED
    assert (nacional.full_time.home, nacional.full_time.away) == (1, 4)
    assert nacional.starts_at == datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
    assert nacional.kickoff_confirmed
    # Penalties: the score may include extra time -> finished, but no result stored.
    assert f["401923295"].status is EventStatus.FINISHED and f["401923295"].full_time is None
    assert f["401896749"].status is EventStatus.POSTPONED
    for live in ("401878048", "401842290", "401842219"):  # 1st half, in progress, half time
        assert f[live].status is EventStatus.LIVE and f[live].full_time is None
    assert f["761880"].status is EventStatus.SCHEDULED
    assert f["401874057"].round == "Group A"  # "U-17 WWC, Group A"
    assert f["401877851"].round is None  # "Colombian Primera A": no round in the note
    assert f["401877851"].home.name == "Boyacá Chicó FC"


def test_odds_only_before_kick_off():
    _, odds = EspnProvider().normalize_scoreboard(raw(SCOREBOARD), leagues())
    # 7 matches not started carry a line; 5 live ones also do and are left out.
    assert sorted(o.fixture_external_id for o in odds) == sorted(
        ["761880", "761879", "761877", "761881", "761878", "761882", "401878030"]
    )
    atlanta = next(o for o in odds if o.fixture_external_id == "761880")
    prices = {p.selection: p.price for p in atlanta.prices}
    assert prices == {
        Selection.HOME: Decimal("3.300"),  # +230
        Selection.DRAW: Decimal("4.000"),  # +300
        Selection.AWAY: Decimal("1.870"),  # -115
    }
    assert {p.bookmaker for p in atlanta.prices} == {"draftkings"}
    assert {p.captured_at for p in atlanta.prices} == {SEEN_AT}  # no quote time: when seen


def test_incomplete_line_is_skipped_not_guessed():
    payload = copy.deepcopy(SCOREBOARD)
    e = next(x for x in payload["events"] if x["id"] == "761880")
    e["competitions"][0]["odds"][0]["moneyline"]["draw"]["close"]["odds"] = "OFF"
    _, odds = EspnProvider().normalize_scoreboard(raw(payload), leagues())
    assert "761880" not in {o.fixture_external_id for o in odds}


def test_league_missing_from_catalogue_is_skipped():
    by_id = leagues()
    del by_id["650"]  # Colombian Primera A
    batches, _ = EspnProvider().normalize_scoreboard(raw(SCOREBOARD), by_id)
    assert "col.1" not in {b.competition.external_id for b in batches}
    assert sum(len(b.fixtures) for b in batches) == 19


@pytest.mark.parametrize(
    "payload", [[], {"events": "x"}, {"events": [{"id": "1"}]}, {"leagues": []}]
)
def test_unexpected_payload_fails_closed(payload):
    with pytest.raises(FormatError):
        EspnProvider().normalize_scoreboard(raw(payload), leagues())


def test_one_unreadable_event_is_skipped_not_the_whole_day():
    payload = copy.deepcopy(SCOREBOARD)
    payload["events"][0]["uid"] = "s:600~e:401878771"  # no league id
    del payload["events"][1]["season"]  # missing field
    # A cup tie still "to be decided": one competitor only.
    payload["events"][2]["competitions"][0]["competitors"].pop()
    batches, _ = EspnProvider().normalize_scoreboard(raw(payload), leagues())
    ids = {f.external_id for b in batches for f in b.fixtures}
    assert len(ids) == 22 - 3
    assert not ids & {e["id"] for e in payload["events"][:3]}


def test_a_mostly_unreadable_day_fails_closed():
    payload = copy.deepcopy(SCOREBOARD)
    for e in payload["events"][:12]:  # 12 of 22 > half
        del e["season"]
    with pytest.raises(FormatError):
        EspnProvider().normalize_scoreboard(raw(payload), leagues())


@pytest.mark.parametrize("time_valid", ["missing", False])
def test_kickoff_time_is_not_confirmed_unless_espn_says_so(time_valid):
    """QA (phase 2 pattern): a missing flag must not become a confirmed kick-off time."""
    payload = copy.deepcopy(SCOREBOARD)
    comp = next(e for e in payload["events"] if e["id"] == "761880")["competitions"][0]
    if time_valid == "missing":
        del comp["timeValid"]
    else:
        comp["timeValid"] = False
    batches, _ = EspnProvider().normalize_scoreboard(raw(payload), leagues())
    f = next(f for b in batches for f in b.fixtures if f.external_id == "761880")
    assert f.kickoff_confirmed is False


def test_only_known_bookmakers_are_prices():
    payload = copy.deepcopy(SCOREBOARD)
    comp = next(e for e in payload["events"] if e["id"] == "761880")["competitions"][0]
    line = comp["odds"][0]
    projection = {**copy.deepcopy(line), "provider": {"id": "1004", "name": "consensus"}}
    comp["odds"] = [projection, line]
    _, odds = EspnProvider().normalize_scoreboard(raw(payload), leagues())
    atlanta = next(o for o in odds if o.fixture_external_id == "761880")
    assert {p.bookmaker for p in atlanta.prices} == {"draftkings"}
    assert len(atlanta.prices) == 3  # the projection is not a second "bookmaker"
    comp["odds"] = [projection]
    _, odds = EspnProvider().normalize_scoreboard(raw(payload), leagues())
    assert "761880" not in {o.fixture_external_id for o in odds}


def test_suspended_is_not_presented_as_postponed():
    payload = copy.deepcopy(SCOREBOARD)
    e = next(e for e in payload["events"] if e["id"] == "401896749")
    e["status"]["type"]["name"] = "STATUS_SUSPENDED"
    batches, _ = EspnProvider().normalize_scoreboard(raw(payload), leagues())
    f = next(f for b in batches for f in b.fixtures if f.external_id == "401896749")
    assert f.status is EventStatus.UNKNOWN


# --- ingestion ---


def test_ingest_skips_laliga_and_stores_prematch_odds(sf):
    outcome = ingest(sf)
    assert outcome.records_written == 20  # 22 minus LaLiga's 2 (football-data owns it)
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Event)) == 20
        colombia = s.scalars(select(Competition).where(Competition.country_name == "Colombia"))
        assert [c.country_code for c in colombia] == ["CO"]
        result = s.scalar(
            select(EventResult).where(EventResult.event_id == event_id(sf, "401877851"))
        )
        assert (result.home_score, result.away_score, result.source) == (1, 4, Provider.ESPN)
    assert n_snapshots(sf) == 7 * 3
    assert event_id(sf, "401878048") is not None  # live Millonarios stored, its line not


def test_a_league_another_source_already_created_is_reused(sf):
    """Real case on the first run: API-Football had created "Colombian Primera A"-like
    names already (USL Championship, Copa Colombia): same unique key -> one competition."""
    with sf() as s:
        s.add(Competition(name="Colombian Primera A", sport="football", country_code="CO",
                          country_name="Colombia"))  # fmt: skip
        s.add(Competition(name="English Premier League", sport="football", country_code=None,
                          country_name="England"))  # fmt: skip
        s.commit()
    ingest(sf)
    with sf() as s:
        names = s.scalars(select(Competition.name)).all()
        assert names.count("Colombian Primera A") == 1
        assert names.count("English Premier League") == 1
        refs = s.scalars(
            select(ExternalRef.external_id).where(
                ExternalRef.source == Provider.ESPN,
                ExternalRef.entity_type == EntityType.COMPETITION,
            )
        ).all()
    assert {"col.1", "eng.1"} <= set(refs)
    assert event_id(sf, "401877851") is not None  # its matches went in, not rolled back


def test_a_failing_league_makes_the_run_fail_but_keeps_the_others(sf, monkeypatch):
    import app.services.ingestion.espn_job as job

    real = job.upsert_batch

    def flaky(s, batch):
        if batch.competition.external_id == "col.1":
            raise RuntimeError("boom")
        return real(s, batch)

    monkeypatch.setattr(job, "upsert_batch", flaky)
    outcome = run_espn_day(
        sf, FakeEspn(), DAY, leagues(), policy=POLICY, odds_refresh=REFRESH,
        skip_league_slugs=frozenset({"esp.1"}),
    )  # fmt: skip
    assert outcome.status is IngestionStatus.FAILED
    assert outcome.records_written == 17  # 20 minus Colombia's 3
    assert "1 of" in outcome.error
    assert event_id(sf, "401878771") is not None


def test_a_database_error_closes_the_run(sf, monkeypatch):
    import app.services.ingestion.espn_job as job

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(job, "store_espn_odds", broken)
    outcome = run_espn_day(sf, FakeEspn(), DAY, leagues(), policy=POLICY, odds_refresh=REFRESH)
    assert outcome.status is IngestionStatus.FAILED
    with sf() as s:
        run = s.get(IngestionRun, outcome.run_id)
        assert run.finished_at is not None and run.status is IngestionStatus.FAILED


def test_reuse_needs_the_same_country_name(sf):
    """Premiership exists in Scotland and Northern Ireland, both without ISO code."""
    from app.services.fixtures_provider.base import CompetitionDTO, FixturesBatch, SeasonDTO
    from app.services.ingestion.fixtures_job import upsert_batch

    with sf() as s:
        for country in ("Scotland", "Northern-Ireland"):
            s.add(Competition(name="Premiership", sport="football", country_code=None,
                              country_name=country))  # fmt: skip
        s.commit()
        scotland = s.scalar(select(Competition.id).where(Competition.country_name == "Scotland"))
    batch = FixturesBatch(
        source=Provider.ESPN,
        competition=CompetitionDTO(
            external_id="sco.1",
            name="Premiership",
            sport="football",
            country_code=None,
            country_name="Scotland",
        ),
        season=SeasonDTO(external_id="sco.1-2026", name="2026"),
        fixtures=[],
    )
    with sf() as s, s.begin():
        upsert_batch(s, batch)
    with sf() as s:
        ref = s.scalar(
            select(ExternalRef.entity_id).where(
                ExternalRef.source == Provider.ESPN, ExternalRef.external_id == "sco.1"
            )
        )
    assert ref == scotland


def test_unchanged_prices_are_not_stored_again_until_refresh(sf):
    ingest(sf)
    ingest(sf, FakeEspn(at=SEEN_AT + timedelta(minutes=30)))
    assert n_snapshots(sf) == 21  # same lines 30 min later: nothing new
    ingest(sf, FakeEspn(at=SEEN_AT + REFRESH))
    assert n_snapshots(sf) == 42  # same lines, 2 h later: stored again (honest "as of")


def test_a_moved_price_stores_the_whole_line(sf):
    ingest(sf)
    payload = copy.deepcopy(SCOREBOARD)
    e = next(x for x in payload["events"] if x["id"] == "761880")
    e["competitions"][0]["odds"][0]["moneyline"]["home"]["close"]["odds"] = "+240"
    ingest(sf, FakeEspn(payload, at=SEEN_AT + timedelta(minutes=30)))
    assert n_snapshots(sf) == 21 + 3
    with sf() as s:
        newest = s.scalars(
            select(OddSnapshot).where(
                OddSnapshot.event_id == event_id(sf, "761880"),
                OddSnapshot.captured_at > SEEN_AT,
            )
        ).all()
    assert {o.selection: o.price for o in newest}[Selection.HOME] == Decimal("3.400")


def test_raw_payload_is_slimmed_on_the_run(sf):
    ingest(sf)
    with sf() as s:
        run = s.scalars(select(IngestionRun).where(IngestionRun.source == Provider.ESPN)).one()
    stored = run.raw_payload
    assert len(stored["events"]) == 22
    first = stored["events"][0]["competitions"][0]
    assert set(first) == {"timeValid", "venue", "altGameNote", "competitors", "odds"}
    assert len(json.dumps(stored)) < len(json.dumps(SCOREBOARD)) / 2


# --- sync pass ---


def test_espn_days_cover_the_last_colombian_hour():
    # Colombia's day ends at 05:00Z, ESPN's (US Eastern) at 04:00Z: one more ESPN day.
    assert espn_days(date(2026, 10, 16), date(2026, 10, 18)) == [
        date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 19),
    ]  # fmt: skip


def test_sync_near_and_wide_windows(sf):
    near = FakeEspn()
    assert sync_espn(cfg(), sf, wide=False, provider=near, pause=0, today=DAY) == EXIT_OK
    assert near.requested == ["leagues", "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19"]
    wide = FakeEspn()
    assert sync_espn(cfg(), sf, wide=True, provider=wide, pause=0, today=DAY) == EXIT_OK
    assert wide.requested[1] == "2026-10-14" and wide.requested[-1] == "2026-10-25"
    assert len(wide.requested) == 1 + 12  # catalogue + (3 back + today + 7 ahead + 1)


def test_wide_pass_when_the_window_is_stale(sf):
    first = FakeEspn()
    assert sync_espn(cfg(), sf, provider=first, pause=0, today=DAY) == EXIT_OK
    assert len(first.requested) == 13  # never had a wide pass: wide
    second = FakeEspn()
    assert sync_espn(cfg(), sf, provider=second, pause=0, today=DAY) == EXIT_OK
    assert len(second.requested) == 5  # wide is fresh: near pass (yesterday..tomorrow)
    with sf() as s:
        jobs = set(s.scalars(select(IngestionRun.job).where(IngestionRun.source == Provider.ESPN)))
    assert jobs == {"espn_leagues", "espn_day", "espn_day_wide"}


def test_settings_refuse_hiding_laliga():
    with pytest.raises(ValueError, match="incompatible"):
        cfg(day_source="espn", fixtures_provider="api_football")
    assert cfg(day_source="api_football", fixtures_provider="api_football")


def test_sync_stops_when_the_catalogue_fails(sf):
    fake = FakeEspn(fail_leagues=True)
    assert sync_espn(cfg(), sf, provider=fake, pause=0, today=DAY) == EXIT_FAILED
    assert fake.requested == ["leagues"]  # no scoreboard without countries
    assert run_espn_leagues(sf, fake, policy=POLICY).status is IngestionStatus.FAILED


def test_sync_respects_its_daily_budget(sf):
    fake = FakeEspn()
    assert (
        sync_espn(cfg(espn_daily_budget=3), sf, wide=False, provider=fake, pause=0, today=DAY)
        == EXIT_FAILED
    )
    assert fake.requested == ["leagues", "2026-10-16", "2026-10-17"]  # 4th refused


# --- API ---

CLOCK = datetime(2026, 10, 17, 19, 0, tzinfo=UTC)  # 14:00 in Colombia


@pytest.fixture
def client(sf):
    def override_db():
        with sf() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: cfg()  # day_source=espn
    app.dependency_overrides[utc_now] = lambda: CLOCK
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_countries_menu_from_espn(client, sf):
    ingest(sf)
    body = client.get("/api/countries", params={"day": "2026-10-17"}).json()
    assert body["source"] == "espn"
    assert body["browse_days"][0] == "2026-10-14" and body["browse_days"][-1] == "2026-10-24"
    countries = {c["name"]: c for c in body["countries"]}
    # Medellín-Once Caldas (01:10Z on the 18th) is still the 17th in Colombia.
    assert countries["Colombia"]["n_events"] == 3
    assert countries["Colombia"]["n_with_odds"] == 1
    assert countries["USA"]["n_events"] == 10
    assert "Spain" not in countries  # LaLiga skipped (football-data owns it)
    assert body["countries"][-1]["name"] == "World"


def test_single_bookmaker_odds_are_labelled_as_such(client, sf):
    ingest(sf)
    events = client.get("/api/events", params={"day": "2026-10-17", "country": "Colombia"}).json()[
        "events"
    ]
    medellin = next(e for e in events if e["id"] == event_id(sf, "401878030"))
    assert medellin["odds"]["source"] == "espn"
    assert medellin["odds"]["n_bookmakers"] == 1
    assert "una sola casa" in medellin["odds"]["method"]
    assert "esa única casa" in medellin["implied_probability"]["method"]
    assert "mercado descuenta" not in medellin["implied_probability"]["method"]
    assert medellin["odds"]["median_price"] == {"home": "1.952", "draw": "3.35", "away": "3.65"}
    assert medellin["can_fetch_odds"] is False  # on-demand odds are API-Football's
    assert "draftkings" not in json.dumps(events).lower()  # INTERNAL MODE: no names
    millonarios = next(e for e in events if e["id"] == event_id(sf, "401878048"))
    assert millonarios["status"] == "live" and millonarios["odds"] is None


def test_only_the_active_day_source_is_shown(client, sf):
    ingest(sf)
    # The same match ingested by the other multi-country source (as after switching).
    with sf() as s:
        comp = s.scalar(select(Competition).where(Competition.country_name == "Colombia"))
        twin = s.scalar(select(Event).where(Event.id == event_id(sf, "401877851")))
        copy_ = Event(
            competition_id=comp.id,
            season_id=twin.season_id,
            home_participant_id=twin.home_participant_id,
            away_participant_id=twin.away_participant_id,
            starts_at=twin.starts_at,
            status=twin.status,
            kickoff_confirmed=True,
        )
        s.add(copy_)
        s.flush()
        s.add(
            ExternalRef(
                source=Provider.API_FOOTBALL, entity_type=EntityType.EVENT,
                external_id="999", entity_id=copy_.id,
            )
        )  # fmt: skip
        s.commit()
        copy_id = copy_.id

    def ids():
        r = client.get("/api/events", params={"day": "2026-10-17", "country": "Colombia"})
        return {e["id"] for e in r.json()["events"]}

    assert copy_id not in ids()
    app.dependency_overrides[get_settings] = lambda: cfg(day_source="api_football")
    assert ids() == {copy_id}
    menu = client.get("/api/countries", params={"day": "2026-10-17"}).json()
    assert [c["name"] for c in menu["countries"]] == ["Colombia"]
    assert menu["browse_days"] == ["2026-10-16", "2026-10-17", "2026-10-18"]
