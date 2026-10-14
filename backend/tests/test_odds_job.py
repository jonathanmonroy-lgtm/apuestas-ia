import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Event, ExternalRef, OddSnapshot, ProviderQuota, UnmatchedOdd
from app.models.enums import EntityType, EventStatus, IngestionStatus, Provider, QuotaPeriod
from app.services.fixtures_provider.football_data import FootballDataProvider
from app.services.http import RawResponse
from app.services.ingestion.fixtures_job import upsert_batch
from app.services.ingestion.odds_job import run_odds_ingestion
from app.services.odds_provider.the_odds_api import TheOddsApiProvider
from app.services.quota import QuotaPolicy

FIXTURES = Path(__file__).parent / "fixtures"
POLICY = QuotaPolicy(QuotaPeriod.MONTH, 450)
ODDS = "the_odds_api/odds_pd_h2h_eu_real.json"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# When the real payloads were recorded: "already started" is judged against this, so the
# tests don't change meaning once the recorded matches are in the past.
RECORDED_AT = datetime(2026, 10, 14, 23, 5, 30, tzinfo=UTC)


def raw(payload, headers=None, fetched_at=RECORDED_AT) -> RawResponse:
    return RawResponse(
        payload=payload, url="https://x", http_status=200, headers=headers or {}, attempts=1,
        fetched_at=fetched_at,
    )  # fmt: skip


class FakeOddsProvider(TheOddsApiProvider):
    """Real normalizer, fake network (real recorded odds payload)."""

    def __init__(self, payload=None, remaining: int = 499):
        self.regions = "eu"
        self.payload = payload if payload is not None else load(ODDS)
        self.remaining = remaining
        self.calls = 0

    def fetch_odds(self, competition):
        self.calls += 1
        return raw(
            self.payload, {"x-requests-last": "1", "x-requests-remaining": str(self.remaining)}
        )

    def close(self):
        pass


@pytest.fixture
def sf(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def calendar(sf):
    """The 3 real football-data matches the recorded odds refer to."""
    payload = load("football_data/matches_pd_2026_real_odds_window.json")
    with sf() as s, s.begin():
        upsert_batch(s, FootballDataProvider("k").normalize(raw(payload)))


def count(sf, model, *where) -> int:
    with sf() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def test_all_real_events_match_and_snapshots_are_stored(sf, calendar):
    outcome = run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)

    assert outcome.status is IngestionStatus.SUCCESS
    assert outcome.records_written == 12 + 12 + 9  # lay prices excluded
    assert count(sf, Event) == 3  # odds never create events
    assert count(sf, UnmatchedOdd) == 0
    with sf() as s:
        # Dépor - Levante odds attached to football-data match 564716
        event_id = s.scalar(
            select(ExternalRef.entity_id).where(
                ExternalRef.source == Provider.THE_ODDS_API,
                ExternalRef.entity_type == EntityType.EVENT,
                ExternalRef.external_id == "19ee731602634e5c9e3106630e087464",
            )
        )
        fd_event_id = s.scalar(
            select(ExternalRef.entity_id).where(
                ExternalRef.source == Provider.FOOTBALL_DATA,
                ExternalRef.entity_type == EntityType.EVENT,
                ExternalRef.external_id == "564716",
            )
        )
        assert event_id == fd_event_id
        # team aliases learned as ExternalRefs for the next runs
        aliases = s.scalars(
            select(ExternalRef.external_id).where(
                ExternalRef.source == Provider.THE_ODDS_API,
                ExternalRef.entity_type == EntityType.PARTICIPANT,
            )
        ).all()
        assert set(aliases) == {"Deportivo La Coruña", "Levante", "Real Betis", "Barcelona",
                                "Real Madrid"}  # fmt: skip


def test_rerun_with_same_prices_inserts_nothing(sf, calendar):
    run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)
    second = run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)

    assert second.status is IngestionStatus.SUCCESS
    assert second.records_written == 0
    assert count(sf, OddSnapshot) == 33


def test_new_price_is_appended_not_overwritten(sf, calendar):
    run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)
    payload = load(ODDS)
    book = payload[0]["bookmakers"][0]
    book["last_update"] = book["markets"][0]["last_update"] = "2026-10-15T09:00:00Z"
    book["markets"][0]["outcomes"][0]["price"] = 2.05
    second = run_odds_ingestion(sf, FakeOddsProvider(payload), "PD", policy=POLICY)

    assert second.records_written == 3
    assert count(sf, OddSnapshot) == 36  # history kept


def test_unknown_team_goes_to_unmatched_once_and_resolves_later(sf, calendar):
    payload = load(ODDS)
    for e in payload:
        if e["home_team"] == "Real Betis":
            e["home_team"] = "Betis Sevilla"  # a name not in the alias table
            for b in e["bookmakers"]:
                for m in b["markets"]:
                    for o in m["outcomes"]:
                        if o["name"] == "Real Betis":
                            o["name"] = "Betis Sevilla"
    run_odds_ingestion(sf, FakeOddsProvider(payload), "PD", policy=POLICY)
    run_odds_ingestion(sf, FakeOddsProvider(payload), "PD", policy=POLICY)

    with sf() as s:
        rows = s.scalars(select(UnmatchedOdd)).all()
        assert len(rows) == 1  # updated, not duplicated, on the second run
        assert "Betis Sevilla" in rows[0].reason
        assert rows[0].resolved_at is None

    run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)  # provider fixes name
    with sf() as s:
        assert s.scalars(select(UnmatchedOdd)).one().resolved_at is not None


def test_odds_before_the_calendar_are_unmatched_not_invented(sf):
    outcome = run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)
    assert outcome.status is IngestionStatus.SUCCESS
    assert count(sf, Event) == 0
    assert count(sf, OddSnapshot) == 0
    assert count(sf, UnmatchedOdd) == 3


def test_cancelled_event_is_not_matched(sf, calendar):
    with sf() as s, s.begin():
        for e in s.scalars(select(Event)):
            e.status = EventStatus.CANCELLED
    run_odds_ingestion(sf, FakeOddsProvider(), "PD", policy=POLICY)
    assert count(sf, OddSnapshot) == 0
    assert count(sf, UnmatchedOdd) == 3


def test_kickoff_disagreement_keeps_the_calendar_time(sf, calendar):
    payload = load(ODDS)
    payload[0]["commence_time"] = "2026-10-16T21:30:00Z"  # 2.5h later than football-data
    run_odds_ingestion(sf, FakeOddsProvider(payload), "PD", policy=POLICY)

    assert count(sf, UnmatchedOdd) == 0
    with sf() as s:
        starts = s.scalars(select(Event.starts_at)).all()
        assert datetime(2026, 10, 16, 19, 0, tzinfo=UTC) in starts


def test_credits_charged_from_header_and_paused_at_zero(sf, calendar):
    run_odds_ingestion(sf, FakeOddsProvider(remaining=0), "PD", policy=POLICY)
    with sf() as s:
        quota = s.get_one(ProviderQuota, Provider.THE_ODDS_API)
        assert quota.used == 1
        assert quota.exhausted_until is not None

    provider = FakeOddsProvider()
    outcome = run_odds_ingestion(sf, provider, "PD", policy=POLICY)
    assert outcome.status is IngestionStatus.SKIPPED_NO_BUDGET
    assert provider.calls == 0


def test_monthly_budget_refuses_before_calling(sf, calendar):
    provider = FakeOddsProvider()
    policy = QuotaPolicy(QuotaPeriod.MONTH, 1)
    run_odds_ingestion(sf, provider, "PD", policy=policy)
    outcome = run_odds_ingestion(sf, provider, "PD", policy=policy)
    assert outcome.status is IngestionStatus.SKIPPED_NO_BUDGET
    assert provider.calls == 1
