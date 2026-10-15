"""Multi-country "matches of the day" ingestion from API-Football (free plan).

- Fixtures: one request per day (Colombia's day) returns every country's matches; each
  league is upserted in its own savepoint so one odd league can't sink the others.
  Leagues owned by another calendar source (LaLiga = football-data) are skipped, so a
  match never exists twice.
- Odds in bulk: the free plan reads at most 3 pages a day (~30 matches).
- Odds on demand: one request for one match, when a user asks for it.
Same rules as every job: budget checked before calling, raw JSON kept on the run, odds
append-only, events never created from odds.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models import ExternalRef
from app.models.enums import EntityType, IngestionStatus, Provider
from app.services.errors import ProviderError
from app.services.fixtures_provider.api_football import ApiFootballProvider, FixtureOdds
from app.services.ingestion.fetch import fetch_with_budget
from app.services.ingestion.fixtures_job import upsert_batch
from app.services.ingestion.odds_job import insert_snapshots
from app.services.ingestion.refs import find_ref
from app.services.ingestion.runs import RunOutcome, RunRecorder
from app.services.quota import QuotaPolicy

log = get_logger(__name__)

JOB_DAY_FIXTURES = "day_fixtures"
JOB_DAY_ODDS = "day_odds"
JOB_EVENT_ODDS = "event_odds"


def run_day_fixtures(
    session_factory: sessionmaker[Session],
    provider: ApiFootballProvider,
    day: date,
    *,
    policy: QuotaPolicy,
    skip_league_ids: frozenset[str] = frozenset(),
) -> RunOutcome:
    recorder = RunRecorder(session_factory, provider.source, JOB_DAY_FIXTURES)
    raw = fetch_with_budget(
        session_factory, recorder, provider.source, policy,
        lambda: provider.fetch_day(day), remaining_of=provider.quota_remaining,
    )  # fmt: skip
    if isinstance(raw, RunOutcome):
        return raw
    try:
        batches = provider.normalize_day(raw)
    except Exception as exc:
        if not isinstance(exc, ProviderError):
            log.exception("day_fixtures_normalize_unexpected_error", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)

    written = failed = skipped = 0
    with session_factory() as s, s.begin():
        for batch in batches:
            if batch.competition.external_id in skip_league_ids:
                skipped += 1
                continue
            try:
                with s.begin_nested():
                    written += upsert_batch(s, batch)
            except Exception:
                failed += 1
                log.exception("day_fixtures_league_failed", league=batch.competition.external_id,
                              run_id=recorder.run_id)  # fmt: skip
    log.info("day_fixtures_stored", day=day.isoformat(), leagues=len(batches),
             skipped=skipped, failed=failed, events=written)  # fmt: skip
    return recorder.finish(IngestionStatus.SUCCESS, records=written)


def store_fixture_odds(s: Session, odds: list[FixtureOdds], run_id: int | None) -> int:
    """Attach odds to events we already know (by the provider's fixture id); never create one."""
    inserted = 0
    for fo in odds:
        event_id = find_ref(s, Provider.API_FOOTBALL, EntityType.EVENT, fo.fixture_external_id)
        if event_id is None or not fo.prices:
            continue  # a league we skip (LaLiga), or a fixture outside what we ingested
        inserted += insert_snapshots(s, Provider.API_FOOTBALL, event_id, fo.prices, run_id)
    return inserted


def _run_odds(
    session_factory: sessionmaker[Session],
    provider: ApiFootballProvider,
    job: str,
    fetch,
    policy: QuotaPolicy,
) -> RunOutcome:
    recorder = RunRecorder(session_factory, provider.source, job)
    raw = fetch_with_budget(
        session_factory, recorder, provider.source, policy, fetch,
        remaining_of=provider.quota_remaining,
    )  # fmt: skip
    if isinstance(raw, RunOutcome):
        return raw
    try:
        odds = provider.normalize_odds(raw)
    except Exception as exc:
        if not isinstance(exc, ProviderError):
            log.exception("odds_normalize_unexpected_error", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)
    with session_factory() as s, s.begin():
        inserted = store_fixture_odds(s, odds, recorder.run_id)
    return recorder.finish(IngestionStatus.SUCCESS, records=inserted)


def run_day_odds_page(
    session_factory: sessionmaker[Session],
    provider: ApiFootballProvider,
    day: date,
    page: int,
    *,
    policy: QuotaPolicy,
) -> RunOutcome:
    return _run_odds(
        session_factory, provider, JOB_DAY_ODDS, lambda: provider.fetch_odds_page(day, page),
        policy,
    )  # fmt: skip


def run_event_odds(
    session_factory: sessionmaker[Session],
    provider: ApiFootballProvider,
    event_id: int,
    *,
    policy: QuotaPolicy,
) -> RunOutcome | None:
    """Odds for ONE match on demand. None if the match isn't an API-Football fixture."""
    with session_factory() as s:
        external_id = s.scalar(
            select(ExternalRef.external_id).where(
                ExternalRef.source == Provider.API_FOOTBALL,
                ExternalRef.entity_type == EntityType.EVENT,
                ExternalRef.entity_id == event_id,
            )
        )
    if external_id is None:
        return None
    return _run_odds(
        session_factory, provider, JOB_EVENT_ODDS,
        lambda: provider.fetch_fixture_odds(external_id), policy,
    )  # fmt: skip
