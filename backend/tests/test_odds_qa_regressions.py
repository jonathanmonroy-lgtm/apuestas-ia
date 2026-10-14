"""Regression tests for the qa-reviewer findings on Phase 3 (odds)."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Event, ExternalRef, OddSnapshot, UnmatchedOdd
from app.models.enums import (
    EntityType,
    IngestionStatus,
    Market,
    Provider,
    QuotaPeriod,
    Selection,
)
from app.services.ingestion.odds_job import run_odds_ingestion
from app.services.odds_provider.team_aliases import ODDS_API_TO_FOOTBALL_DATA as ALIASES
from app.services.odds_provider.the_odds_api import TheOddsApiProvider
from app.services.quota import QuotaPolicy
from tests.test_events_api import client, get, with_odds  # noqa: F401 - fixtures
from tests.test_odds_job import (  # noqa: F401 - fixtures
    FIXTURES,
    ODDS,
    POLICY,
    FakeOddsProvider,
    calendar,
    count,
    load,
    raw,
    sf,
)

DEPOR_LEVANTE = "19ee731602634e5c9e3106630e087464"  # kicks off 2026-10-16T19:00Z


# --- #1 live odds mixed with pre-match ---


def test_started_event_is_dropped():
    after_kickoff = datetime(2026, 10, 16, 19, 1, tzinfo=UTC)
    batch = TheOddsApiProvider("k").normalize(raw(load(ODDS), fetched_at=after_kickoff), "PD")
    assert DEPOR_LEVANTE not in {e.external_id for e in batch.events}


def test_in_play_quote_inside_upcoming_event_is_dropped():
    payload = load(ODDS)
    book = payload[0]["bookmakers"][0]
    book["last_update"] = book["markets"][0]["last_update"] = "2026-10-16T19:20:00Z"
    batch = TheOddsApiProvider("k").normalize(raw(payload), "PD")
    assert book["key"] not in {p.bookmaker for p in batch.events[0].prices}
    assert len(batch.events[0].prices) == 9


def test_api_ignores_quotes_stamped_after_kickoff(client, sf, calendar):  # noqa: F811
    with sf() as s, s.begin():
        event = s.scalars(select(Event).order_by(Event.starts_at)).first()
        for sel, price in ((Selection.HOME, "1.10"), (Selection.DRAW, "9"), (Selection.AWAY, "30")):
            s.add(
                OddSnapshot(
                    event_id=event.id, source=Provider.THE_ODDS_API, bookmaker="pinnacle",
                    market=Market.H2H, selection=sel, price=Decimal(price),
                    captured_at=event.starts_at + timedelta(minutes=50),
                )
            )  # fmt: skip
    assert get(client)["events"][0]["odds"] is None


# --- #2 one bad price must not fail the whole run ---


def test_invalid_price_drops_one_bookmaker_not_the_batch():
    payload = load(ODDS)
    payload[0]["bookmakers"][1]["markets"][0]["outcomes"][0]["price"] = 1.0
    batch = TheOddsApiProvider("k").normalize(raw(payload), "PD")
    assert len(batch.events) == 3
    assert len(batch.events[0].prices) == 9
    assert all(p.price > 1 for e in batch.events for p in e.prices)


# --- #4 single source in the summary ---


def test_other_sources_are_not_mixed_into_the_summary(client, sf, with_odds):  # noqa: F811
    with sf() as s, s.begin():
        event = s.scalars(select(Event).order_by(Event.starts_at)).first()
        for sel in Selection:
            s.add(
                OddSnapshot(
                    event_id=event.id, source=Provider.API_FOOTBALL, bookmaker="x",
                    market=Market.H2H, selection=sel, price=Decimal("50"),
                    captured_at=datetime(2026, 10, 14, 23, 5, 5, tzinfo=UTC),
                )
            )  # fmt: skip
    dep = get(client)["events"][0]
    assert dep["odds"]["n_bookmakers"] == 3
    assert dep["odds"]["source"] == "the_odds_api"


# --- #5 cost and reserve ---


def test_expected_cost_follows_configured_regions():
    assert TheOddsApiProvider("k", regions="eu").expected_cost() == 1
    assert TheOddsApiProvider("k", regions="eu,uk").expected_cost() == 2


def test_provider_balance_reserve_stops_the_worker(sf, calendar):  # noqa: F811
    policy = QuotaPolicy(QuotaPeriod.MONTH, 450, reserve=50)
    run_odds_ingestion(sf, FakeOddsProvider(remaining=50), "PD", policy=policy)
    provider = FakeOddsProvider()
    outcome = run_odds_ingestion(sf, provider, "PD", policy=policy)
    assert outcome.status is IngestionStatus.SKIPPED_NO_BUDGET
    assert provider.calls == 0


# --- #6 aliases verifiable and refs re-checked ---


def test_alias_table_covers_every_real_team_and_kickoff():
    ref = json.loads(
        (FIXTURES / "the_odds_api/team_names_2026_real.json").read_text(encoding="utf-8")
    )
    odds_names = {n for e in ref["odds_api_events"] for n in (e["home_team"], e["away_team"])}
    assert odds_names == set(ALIASES)  # every name the provider used has an alias
    assert set(ALIASES.values()) == set(ref["football_data_teams"])  # 20 distinct teams
    fd = {
        (m["utcDate"], m["home_id"], m["away_id"])
        for m in ref["football_data_matches_same_kickoffs"]
    }
    for e in ref["odds_api_events"]:  # all 21 real events land on the right match
        assert (e["commence_time"], ALIASES[e["home_team"]], ALIASES[e["away_team"]]) in fd, e


def _odds_team_ref(s, name: str) -> ExternalRef:
    return s.scalar(
        select(ExternalRef).where(
            ExternalRef.source == Provider.THE_ODDS_API,
            ExternalRef.entity_type == EntityType.PARTICIPANT,
            ExternalRef.external_id == name,
        )
    )


def test_wrong_learned_alias_is_repointed(sf, calendar):  # noqa: F811
    run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)
    with sf() as s, s.begin():
        correct = _odds_team_ref(s, "Levante").entity_id
        _odds_team_ref(s, "Levante").entity_id = _odds_team_ref(s, "Real Madrid").entity_id
    run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)
    with sf() as s:
        assert _odds_team_ref(s, "Levante").entity_id == correct


def test_event_link_with_wrong_teams_goes_to_unmatched(sf, calendar):  # noqa: F811
    run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)
    with sf() as s, s.begin():
        ref = s.scalar(select(ExternalRef).where(ExternalRef.external_id == DEPOR_LEVANTE))
        ref.entity_id = s.scalar(select(Event.id).where(Event.id != ref.entity_id).limit(1))
    before = count(sf, OddSnapshot)
    run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)
    assert count(sf, UnmatchedOdd, UnmatchedOdd.resolved_at.is_(None)) == 1
    assert count(sf, OddSnapshot) == before  # nothing attached to the wrong match


# --- #7 data_as_of reflects stored quotes, not just "a run ran" ---


def test_data_as_of_odds_is_null_when_nothing_was_stored(client, sf):  # noqa: F811
    outcome = run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)
    assert outcome.status is IngestionStatus.SUCCESS  # the run did succeed...
    assert get(client)["data_as_of"]["odds"] is None  # ...but no quote was stored
