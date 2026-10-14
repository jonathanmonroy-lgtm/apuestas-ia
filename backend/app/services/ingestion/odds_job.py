"""Odds ingestion: provider -> IngestionRun (raw) -> OddSnapshot (append-only).

Event matching rules (the fixtures provider is the source of truth for Event):
- Odds NEVER create or modify an Event. No match -> `unmatched_odd`, for a human to fix.
- An odds event already linked (ExternalRef event) is reused directly.
- Otherwise both team names must resolve to Participants, via an existing ExternalRef or
  the hand-curated alias table (never fuzzy matching). Then exactly one Event with that
  home/away pair must start within MATCH_WINDOW of the odds' commence_time. In a league
  a home/away pair meets once per season, so the window only guards against a stale
  odds event or a fixture moved by weeks; it is not the identity criterion.
- Kick-off disagreements are logged, never "fixed": Event keeps the calendar's time.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models import Event, OddSnapshot, UnmatchedOdd
from app.models.enums import EntityType, EventStatus, IngestionStatus, Provider
from app.services.errors import ProviderError
from app.services.ingestion.fetch import fetch_with_budget
from app.services.ingestion.refs import add_ref, find_ref
from app.services.ingestion.runs import RunOutcome, RunRecorder
from app.services.odds_provider.base import OddsBatch, OddsEventDTO, OddsProvider
from app.services.odds_provider.team_aliases import ODDS_API_TO_FOOTBALL_DATA
from app.services.quota import QuotaPolicy

log = get_logger(__name__)

JOB_ODDS = "odds"
MATCH_WINDOW = timedelta(days=3)
KICKOFF_TOLERANCE = timedelta(hours=2)
# Where the alias table points: the fixtures provider that owns Participant identities.
ALIAS_TARGET = Provider.FOOTBALL_DATA


def run_odds_ingestion(
    session_factory: sessionmaker[Session],
    provider: OddsProvider,
    competition_code: str,
    *,
    policy: QuotaPolicy,
    expected_cost: int = 1,
) -> RunOutcome:
    recorder = RunRecorder(session_factory, provider.source, JOB_ODDS)
    raw = fetch_with_budget(
        session_factory,
        recorder,
        provider.source,
        policy,
        lambda: provider.fetch_odds(competition_code),
        expected_cost=expected_cost,
        cost_of=provider.request_cost,
        remaining_of=provider.quota_remaining,
    )
    if isinstance(raw, RunOutcome):
        return raw

    try:
        batch = provider.normalize(raw, competition_code)
    except Exception as exc:
        if not isinstance(exc, ProviderError):
            log.exception("odds_normalize_unexpected_error", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)

    try:
        with session_factory() as s, s.begin():
            stats = store_batch(s, batch, run_id=recorder.run_id)
    except Exception as exc:  # rolled back as a whole
        log.exception("odds_store_failed", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)

    log.info("odds_batch_stored", run_id=recorder.run_id, **stats)
    return recorder.finish(IngestionStatus.SUCCESS, records=stats["snapshots_inserted"])


def store_batch(s: Session, batch: OddsBatch, *, run_id: int | None) -> dict[str, int]:
    stats = {"events_matched": 0, "events_unmatched": 0, "snapshots_inserted": 0}
    for dto in batch.events:
        event_id, reason = match_event(s, batch.source, dto)
        if event_id is None:
            record_unmatched(s, batch.source, dto, reason, run_id)
            stats["events_unmatched"] += 1
            continue
        resolve_unmatched(s, batch.source, dto.external_id)
        stats["events_matched"] += 1
        stats["snapshots_inserted"] += insert_snapshots(s, batch.source, event_id, dto, run_id)
    return stats


def match_event(s: Session, source: Provider, dto: OddsEventDTO) -> tuple[int | None, str]:
    """Returns (event_id, "") or (None, reason)."""
    linked = find_ref(s, source, EntityType.EVENT, dto.external_id)
    if linked is not None:
        return linked, ""

    home_id = participant_for(s, source, dto.home_name)
    away_id = participant_for(s, source, dto.away_name)
    unknown = [n for n, pid in ((dto.home_name, home_id), (dto.away_name, away_id)) if not pid]
    if unknown:
        return None, f"unknown team name(s): {', '.join(unknown)}"

    candidates = s.scalars(
        select(Event).where(
            Event.home_participant_id == home_id,
            Event.away_participant_id == away_id,
            Event.starts_at >= dto.commence_time - MATCH_WINDOW,
            Event.starts_at <= dto.commence_time + MATCH_WINDOW,
            Event.status != EventStatus.CANCELLED,
        )
    ).all()
    if not candidates:
        return None, "no event with these teams within ±3 days"
    if len(candidates) > 1:
        return None, f"ambiguous: {len(candidates)} events match"

    event = candidates[0]
    if event.kickoff_confirmed and abs(event.starts_at - dto.commence_time) > KICKOFF_TOLERANCE:
        log.warning(
            "odds_kickoff_mismatch",
            event_id=event.id, calendar=event.starts_at.isoformat(),
            odds=dto.commence_time.isoformat(),
        )  # fmt: skip
    add_ref(s, source, EntityType.EVENT, dto.external_id, event.id)
    s.flush()
    return event.id, ""


def participant_for(s: Session, source: Provider, name: str) -> int | None:
    pid = find_ref(s, source, EntityType.PARTICIPANT, name)
    if pid is not None:
        return pid
    target_id = ODDS_API_TO_FOOTBALL_DATA.get(name)
    if target_id is None:
        return None
    pid = find_ref(s, ALIAS_TARGET, EntityType.PARTICIPANT, target_id)
    if pid is None:
        return None  # alias known, but that team was never ingested from the calendar
    add_ref(s, source, EntityType.PARTICIPANT, name, pid, raw_name=name)
    s.flush()
    return pid


def insert_snapshots(
    s: Session, source: Provider, event_id: int, dto: OddsEventDTO, run_id: int | None
) -> int:
    existing = set(
        s.execute(
            select(
                OddSnapshot.bookmaker,
                OddSnapshot.market,
                OddSnapshot.selection,
                OddSnapshot.captured_at,
            ).where(OddSnapshot.event_id == event_id, OddSnapshot.source == source)
        ).all()
    )
    inserted = 0
    for p in dto.prices:
        key = (p.bookmaker, p.market, p.selection, p.captured_at.astimezone(UTC))
        if key in existing:
            continue  # same observation already stored (append-only, idempotent)
        s.add(
            OddSnapshot(
                event_id=event_id, source=source, bookmaker=p.bookmaker, market=p.market,
                selection=p.selection, price=p.price, captured_at=p.captured_at,
                ingestion_run_id=run_id,
            )
        )  # fmt: skip
        existing.add(key)
        inserted += 1
    return inserted


def record_unmatched(
    s: Session, source: Provider, dto: OddsEventDTO, reason: str, run_id: int | None
) -> None:
    """One open row per provider event: updated on every run, not duplicated."""
    row = s.scalars(
        select(UnmatchedOdd).where(
            UnmatchedOdd.source == source,
            UnmatchedOdd.external_event_id == dto.external_id,
            UnmatchedOdd.resolved_at.is_(None),
        )
    ).first()
    if row is None:
        row = UnmatchedOdd(source=source, external_event_id=dto.external_id)
        s.add(row)
    row.home_name = dto.home_name
    row.away_name = dto.away_name
    row.commence_time = dto.commence_time
    row.reason = reason[:200]
    row.payload = dto.model_dump(mode="json")
    row.ingestion_run_id = run_id
    log.warning("odds_event_unmatched", external_id=dto.external_id, reason=reason)


def resolve_unmatched(s: Session, source: Provider, external_id: str) -> None:
    for row in s.scalars(
        select(UnmatchedOdd).where(
            UnmatchedOdd.source == source,
            UnmatchedOdd.external_event_id == external_id,
            UnmatchedOdd.resolved_at.is_(None),
        )
    ):
        row.resolved_at = datetime.now(UTC)
