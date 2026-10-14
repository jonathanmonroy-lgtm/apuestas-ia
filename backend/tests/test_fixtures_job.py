import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Event, EventResult, IngestionRun, Participant, ProviderQuota
from app.models.enums import EntityType, EventStatus, IngestionStatus, Provider, QuotaPeriod
from app.services.errors import AuthError, ProviderError
from app.services.fixtures_provider.football_data import FootballDataProvider
from app.services.http import RawResponse
from app.services.ingestion.fixtures_job import run_fixtures_ingestion
from app.services.ingestion.refs import find_ref
from app.services.quota import QuotaPolicy

PAYLOAD = json.loads(
    (Path(__file__).parent / "fixtures/football_data/matches_pd_2026_real.json").read_text(
        encoding="utf-8"
    )
)
POLICY = QuotaPolicy(QuotaPeriod.DAY, 10)


class FakeProvider(FootballDataProvider):
    """Real normalizer, fake network: returns `payload` or raises `error`."""

    def __init__(self, payload=None, error: ProviderError | None = None):
        self.payload = payload if payload is not None else copy.deepcopy(PAYLOAD)
        self.error = error
        self.calls = 0

    def fetch_fixtures(self, competition, **kw) -> RawResponse:
        self.calls += 1
        if self.error:
            raise self.error
        return RawResponse(
            payload=self.payload, url="https://fake/matches", http_status=200, headers={},
            attempts=1,
        )  # fmt: skip

    def close(self) -> None:
        pass


@pytest.fixture
def sf(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


def count(sf, model) -> int:
    with sf() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_success_writes_events_results_and_raw(sf):
    outcome = run_fixtures_ingestion(sf, FakeProvider(), "PD", policy=POLICY)

    assert outcome.status is IngestionStatus.SUCCESS
    assert outcome.records_written == 5
    assert count(sf, Event) == 5
    assert count(sf, Participant) == 8
    assert count(sf, EventResult) == 2  # the cancelled match's 0-0 is not a result
    with sf() as s:
        run = s.get_one(IngestionRun, outcome.run_id)
        assert run.raw_payload["matches"][0]["id"] == 564634
        assert run.source is Provider.FOOTBALL_DATA


def test_rerun_is_idempotent_and_applies_updates(sf):
    run_fixtures_ingestion(sf, FakeProvider(), "PD", policy=POLICY)

    updated = copy.deepcopy(PAYLOAD)
    m = updated["matches"][2]  # Deportivo - Levante (TIMED) gets played
    m["status"] = "FINISHED"
    m["score"]["fullTime"] = {"home": 0, "away": 0}
    m["score"]["halfTime"] = {"home": 0, "away": 0}
    outcome = run_fixtures_ingestion(sf, FakeProvider(updated), "PD", policy=POLICY)

    assert outcome.status is IngestionStatus.SUCCESS
    assert count(sf, Event) == 5
    assert count(sf, Participant) == 8
    assert count(sf, EventResult) == 3
    with sf() as s:
        statuses = s.scalars(select(Event.status)).all()
        assert statuses.count(EventStatus.FINISHED) == 3


def test_no_budget_skips_without_calling_provider(sf):
    provider = FakeProvider()
    policy = QuotaPolicy(QuotaPeriod.DAY, 1)
    run_fixtures_ingestion(sf, provider, "PD", policy=policy)
    outcome = run_fixtures_ingestion(sf, provider, "PD", policy=policy)

    assert outcome.status is IngestionStatus.SKIPPED_NO_BUDGET
    assert provider.calls == 1


def test_format_error_keeps_raw_and_writes_no_data(sf):
    broken = {"competition": {"id": 2014, "name": "x"}, "matches": [{"id": "nope"}]}
    outcome = run_fixtures_ingestion(sf, FakeProvider(broken), "PD", policy=POLICY)

    assert outcome.status is IngestionStatus.FAILED
    assert "FormatError" in outcome.error
    assert count(sf, Event) == 0
    with sf() as s:
        assert s.get_one(IngestionRun, outcome.run_id).raw_payload == broken


def test_provider_error_maps_to_run_status(sf):
    provider = FakeProvider(error=AuthError("bad key", http_status=401, attempts=1))
    outcome = run_fixtures_ingestion(sf, provider, "PD", policy=POLICY)

    assert outcome.status is IngestionStatus.AUTH_ERROR
    with sf() as s:
        assert s.get_one(IngestionRun, outcome.run_id).http_status == 401


def test_kickoff_gets_confirmed_when_provider_sets_the_time(sf):
    run_fixtures_ingestion(sf, FakeProvider(), "PD", policy=POLICY)
    with sf() as s:
        event = s.get_one(Event, event_id(s, "564748"))
        assert event.kickoff_confirmed is False
        assert event.starts_at == datetime(2026, 11, 22, 0, 0, tzinfo=UTC)

    updated = copy.deepcopy(PAYLOAD)
    m = updated["matches"][3]  # Athletic - Espanyol: LaLiga announces the time
    m["status"] = "TIMED"
    m["utcDate"] = "2026-11-22T15:15:00Z"
    run_fixtures_ingestion(sf, FakeProvider(updated), "PD", policy=POLICY)

    with sf() as s:
        event = s.get_one(Event, event_id(s, "564748"))
        assert event.kickoff_confirmed is True
        assert event.starts_at == datetime(2026, 11, 22, 15, 15, tzinfo=UTC)
        assert event.status is EventStatus.SCHEDULED


def event_id(s, external_id: str) -> int:
    return find_ref(s, Provider.FOOTBALL_DATA, EntityType.EVENT, external_id)


class ExplodingProvider(FakeProvider):
    def __init__(self, where: str):
        super().__init__()
        self.where = where

    def fetch_fixtures(self, competition, **kw):
        if self.where == "fetch":
            raise RuntimeError("httpx.DecodingError or similar")
        return super().fetch_fixtures(competition, **kw)

    def normalize(self, raw):
        if self.where == "normalize":
            raise RuntimeError("normalizer bug")
        return super().normalize(raw)


@pytest.mark.parametrize("where", ["fetch", "normalize"])
def test_unexpected_exception_never_leaves_run_running(sf, where):
    """QA F2 #6: non-ProviderError exceptions must still close the run."""
    outcome = run_fixtures_ingestion(sf, ExplodingProvider(where), "PD", policy=POLICY)
    assert outcome.status is IngestionStatus.FAILED
    with sf() as s:
        run = s.get_one(IngestionRun, outcome.run_id)
        assert run.status is IngestionStatus.FAILED
        assert run.finished_at is not None
        assert "RuntimeError" in run.error_message


def test_retries_count_against_the_budget(sf):
    """QA F2 #5: 3 attempts = 3 real requests."""

    class RetryingProvider(FakeProvider):
        def fetch_fixtures(self, competition, **kw):
            raw = super().fetch_fixtures(competition, **kw)
            return RawResponse(payload=raw.payload, url=raw.url, http_status=200, headers={},
                               attempts=3)  # fmt: skip

    run_fixtures_ingestion(sf, RetryingProvider(), "PD", policy=POLICY)
    run_fixtures_ingestion(sf, FakeProvider(error=AuthError("x", attempts=2)), "PD", policy=POLICY)
    with sf() as s:
        assert s.get_one(ProviderQuota, Provider.FOOTBALL_DATA).used == 5


def test_result_removed_when_provider_unfinishes_a_match(sf):
    """QA F2 #8: a premature FINISHED corrected to IN_PLAY must not keep a final score."""
    run_fixtures_ingestion(sf, FakeProvider(), "PD", policy=POLICY)
    assert count(sf, EventResult) == 2

    corrected = copy.deepcopy(PAYLOAD)
    corrected["matches"][0]["status"] = "IN_PLAY"
    run_fixtures_ingestion(sf, FakeProvider(corrected), "PD", policy=POLICY)

    assert count(sf, EventResult) == 1
    with sf() as s:
        event = s.get_one(Event, event_id(s, "564634"))
        assert event.status is EventStatus.LIVE
        assert event.result is None


def test_team_country_is_not_inferred_from_the_league(sf):
    """QA F2 #4: a club's country isn't the league's country."""
    run_fixtures_ingestion(sf, FakeProvider(), "PD", policy=POLICY)
    with sf() as s:
        assert s.scalars(select(Participant.country_code).distinct()).all() == [None]
