"""Multi-country "matches of the day" ingestion from ESPN (unofficial, no key).

- League catalogue: one request per sync pass (id -> slug -> country).
- Scoreboard: one request per ESPN day returns every league's matches; each league is
  upserted in its own savepoint so one odd league can't sink the others. Leagues owned by
  another calendar source (LaLiga = football-data) are skipped: a match never exists twice.
- Odds come inside the scoreboard (one bookmaker, matches not started only). ESPN gives
  no quote time, so each pass would re-store the same price: a price is only stored again
  when it changed or the last stored one is older than `odds_refresh` (so "as of" stays
  honest without one row per pass).
Same rules as every job: budget checked before calling, raw JSON (slimmed) kept on the
run, odds append-only, events never created from odds.
"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models import OddSnapshot
from app.models.enums import EntityType, IngestionStatus, Market
from app.services.errors import ProviderError
from app.services.fixtures_provider.api_football import FixtureOdds
from app.services.fixtures_provider.espn import (
    EspnProvider,
    League,
    slim_leagues,
    slim_scoreboard,
)
from app.services.ingestion.fetch import fetch_with_budget
from app.services.ingestion.fixtures_job import upsert_batch
from app.services.ingestion.odds_job import insert_snapshots
from app.services.ingestion.refs import find_ref
from app.services.ingestion.runs import RunOutcome, RunRecorder
from app.services.odds_provider.base import PriceDTO
from app.services.quota import QuotaPolicy

log = get_logger(__name__)

JOB_ESPN_LEAGUES = "espn_leagues"
JOB_ESPN_DAY = "espn_day"


def run_espn_leagues(
    session_factory: sessionmaker[Session], provider: EspnProvider, *, policy: QuotaPolicy
) -> dict[str, League] | RunOutcome:
    """The league catalogue, or the finished (failed/skipped) run."""
    recorder = RunRecorder(session_factory, provider.source, JOB_ESPN_LEAGUES)
    raw = fetch_with_budget(
        session_factory, recorder, provider.source, policy, provider.fetch_leagues,
        redact=slim_leagues,
    )  # fmt: skip
    if isinstance(raw, RunOutcome):
        return raw
    try:
        leagues = provider.normalize_leagues(raw)
    except Exception as exc:
        if not isinstance(exc, ProviderError):
            log.exception("espn_leagues_normalize_unexpected_error", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)
    recorder.finish(IngestionStatus.SUCCESS, records=len(leagues))
    return leagues


def run_espn_day(
    session_factory: sessionmaker[Session],
    provider: EspnProvider,
    day: date,
    leagues: dict[str, League],
    *,
    policy: QuotaPolicy,
    odds_refresh: timedelta,
    skip_league_slugs: frozenset[str] = frozenset(),
) -> RunOutcome:
    recorder = RunRecorder(session_factory, provider.source, JOB_ESPN_DAY)
    raw = fetch_with_budget(
        session_factory, recorder, provider.source, policy,
        lambda: provider.fetch_scoreboard(day), redact=slim_scoreboard,
    )  # fmt: skip
    if isinstance(raw, RunOutcome):
        return raw
    try:
        batches, odds = provider.normalize_scoreboard(raw, leagues)
    except Exception as exc:
        if not isinstance(exc, ProviderError):
            log.exception("espn_day_normalize_unexpected_error", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)

    written = failed = skipped = 0
    with session_factory() as s, s.begin():
        for batch in batches:
            if batch.competition.external_id in skip_league_slugs:
                skipped += 1
                continue
            try:
                with s.begin_nested():
                    written += upsert_batch(s, batch)
            except Exception:
                failed += 1
                log.exception("espn_day_league_failed", league=batch.competition.external_id,
                              run_id=recorder.run_id)  # fmt: skip
        inserted = store_espn_odds(s, odds, recorder.run_id, odds_refresh)
    log.info("espn_day_stored", day=day.isoformat(), leagues=len(batches), skipped=skipped,
             failed=failed, events=written, odds_inserted=inserted)  # fmt: skip
    return recorder.finish(IngestionStatus.SUCCESS, records=written)


def store_espn_odds(
    s: Session, odds: list[FixtureOdds], run_id: int | None, refresh: timedelta
) -> int:
    """Attach odds to events we already know (skipped leagues have none); never create one."""
    inserted = 0
    for fo in odds:
        event_id = find_ref(s, EspnProvider.source, EntityType.EVENT, fo.fixture_external_id)
        if event_id is None:
            continue
        # The three prices travel together: one moved -> store the whole 1X2 line again.
        if any(_worth_storing(s, event_id, p, refresh) for p in fo.prices):
            inserted += insert_snapshots(s, EspnProvider.source, event_id, fo.prices, run_id)
    return inserted


def _worth_storing(s: Session, event_id: int, p: PriceDTO, refresh: timedelta) -> bool:
    last_at = s.scalar(
        select(func.max(OddSnapshot.captured_at)).where(
            OddSnapshot.event_id == event_id,
            OddSnapshot.source == EspnProvider.source,
            OddSnapshot.bookmaker == p.bookmaker,
            OddSnapshot.market == Market.H2H,
            OddSnapshot.selection == p.selection,
        )
    )
    if last_at is None or p.captured_at - last_at >= refresh:
        return True
    last_price = s.scalar(
        select(OddSnapshot.price).where(
            OddSnapshot.event_id == event_id,
            OddSnapshot.source == EspnProvider.source,
            OddSnapshot.bookmaker == p.bookmaker,
            OddSnapshot.market == Market.H2H,
            OddSnapshot.selection == p.selection,
            OddSnapshot.captured_at == last_at,
        )
    )
    return last_price != p.price
