"""Fixtures/results ingestion: provider -> IngestionRun (raw) -> Event/EventResult.

Flow:
1-2. fetch_with_budget: budget check, call, charge the budget, store the raw JSON on the
     run *before* normalizing.
3. Normalize (pure). On FormatError the run is FAILED and no data is written.
4. Upsert everything in ONE transaction: all or nothing.
The API never calls providers; if this job fails, the API keeps serving the last data.
"""

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models import Competition, Event, EventResult, Participant, Season
from app.models.enums import EntityType, EventStatus, IngestionStatus
from app.services.errors import ProviderError
from app.services.fixtures_provider.base import FixturesBatch, FixturesProvider, TeamDTO
from app.services.ingestion.fetch import fetch_with_budget
from app.services.ingestion.refs import add_ref, find_ref
from app.services.ingestion.runs import RunOutcome, RunRecorder
from app.services.quota import QuotaPolicy

log = get_logger(__name__)

JOB_FIXTURES = "fixtures"
JOB_RESULTS = "results"


def run_fixtures_ingestion(
    session_factory: sessionmaker[Session],
    provider: FixturesProvider,
    competition_code: str,
    *,
    policy: QuotaPolicy,
    job: str = JOB_FIXTURES,
    season: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> RunOutcome:
    recorder = RunRecorder(session_factory, provider.source, job)
    raw = fetch_with_budget(
        session_factory,
        recorder,
        provider.source,
        policy,
        lambda: provider.fetch_fixtures(
            competition_code, season=season, date_from=date_from, date_to=date_to
        ),
        remaining_of=provider.quota_remaining,
    )
    if isinstance(raw, RunOutcome):
        return raw

    try:
        batch = provider.normalize(raw)
    except Exception as exc:  # FormatError, or a normalizer bug: raw is already saved
        if not isinstance(exc, ProviderError):
            log.exception("fixtures_normalize_unexpected_error", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)

    try:
        with session_factory() as s, s.begin():
            written = upsert_batch(s, batch)
    except Exception as exc:  # DB constraint, bug... rolled back as a whole
        log.exception("fixtures_upsert_failed", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)

    return recorder.finish(IngestionStatus.SUCCESS, records=written)


def upsert_batch(s: Session, batch: FixturesBatch) -> int:
    """Insert or update competition, season, teams, events and results. Returns #events."""
    src = batch.source
    comp_dto = batch.competition

    comp_id = find_ref(s, src, EntityType.COMPETITION, comp_dto.external_id)
    if comp_id is None:
        # Another source may already know this league under the same unique key (ESPN and
        # API-Football both list "USL Championship", USA): one competition, two refs.
        same = s.scalars(
            select(Competition.id).where(
                Competition.sport == comp_dto.sport,
                Competition.country_name.is_(None)
                if comp_dto.country_name is None
                else Competition.country_name == comp_dto.country_name,
                # England & co. have no ISO code: NULL must match NULL here.
                Competition.country_code.is_(None)
                if comp_dto.country_code is None
                else Competition.country_code == comp_dto.country_code,
                Competition.name == comp_dto.name,
            )
        ).all()
        # Exactly one match, same country NAME too ("Premiership" exists in Scotland and
        # in Northern Ireland, both without ISO code): anything else -> a new competition.
        comp_id = same[0] if len(same) == 1 else None
        if len(same) > 1:
            log.warning("competition_reuse_ambiguous", name=comp_dto.name,
                        country=comp_dto.country_name, candidates=len(same))  # fmt: skip
        if comp_id is not None:
            add_ref(s, src, EntityType.COMPETITION, comp_dto.external_id, comp_id, comp_dto.name)
    if comp_id is None:
        comp = Competition(
            name=comp_dto.name,
            sport=comp_dto.sport,
            country_code=comp_dto.country_code,
            country_name=comp_dto.country_name,
        )
        s.add(comp)
        s.flush()
        add_ref(s, src, EntityType.COMPETITION, comp_dto.external_id, comp.id, comp_dto.name)
        comp_id = comp.id

    season_dto = batch.season
    season_id = find_ref(s, src, EntityType.SEASON, season_dto.external_id)
    if season_id is None:
        season_id = s.scalar(
            select(Season.id).where(
                Season.competition_id == comp_id, Season.name == season_dto.name
            )
        )
        if season_id is not None:
            add_ref(s, src, EntityType.SEASON, season_dto.external_id, season_id, season_dto.name)
    if season_id is None:
        season = Season(
            competition_id=comp_id,
            name=season_dto.name,
            start_date=season_dto.start_date,
            end_date=season_dto.end_date,
        )
        s.add(season)
        s.flush()
        add_ref(s, src, EntityType.SEASON, season_dto.external_id, season.id, season_dto.name)
        season_id = season.id

    team_cache: dict[str, int] = {}

    def team_id(dto: TeamDTO) -> int:
        if dto.external_id in team_cache:
            return team_cache[dto.external_id]
        pid = find_ref(s, src, EntityType.PARTICIPANT, dto.external_id)
        if pid is None:
            p = Participant(
                name=dto.short_name or dto.name,
                short_name=dto.short_name,
                sport=comp_dto.sport,
                # Unknown: a club's country is not the league's (e.g. Monaco in Ligue 1),
                # and we don't invent it.
                country_code=None,
            )
            s.add(p)
            s.flush()
            add_ref(s, src, EntityType.PARTICIPANT, dto.external_id, p.id, dto.name)
            pid = p.id
        team_cache[dto.external_id] = pid
        return pid

    now = datetime.now(UTC)
    for f in batch.fixtures:
        home_id, away_id = team_id(f.home), team_id(f.away)
        event_id = find_ref(s, src, EntityType.EVENT, f.external_id)
        if event_id is None:
            event = Event(competition_id=comp_id, season_id=season_id)
            s.add(event)
        else:
            event = s.get_one(Event, event_id)
        # The fixtures provider is the source of truth: kick-off/status may change.
        event.home_participant_id = home_id
        event.away_participant_id = away_id
        event.starts_at = f.starts_at
        event.kickoff_confirmed = f.kickoff_confirmed
        event.status = f.status
        event.round = f.round
        event.venue = f.venue
        s.flush()
        if event_id is None:
            add_ref(s, src, EntityType.EVENT, f.external_id, event.id)

        existing_result = s.query(EventResult).filter_by(event_id=event.id).one_or_none()
        if f.full_time is None and existing_result is not None:
            # Provider withdrew the result (premature FINISHED corrected, awarded, etc.):
            # a stale "final" score must not survive on an event that isn't finished.
            log.warning("event_result_removed", event_id=event.id, status=f.status.value)
            s.delete(existing_result)
        if f.status is EventStatus.FINISHED and f.full_time is not None:
            result = existing_result
            if result is None:
                result = EventResult(event_id=event.id, source=src, recorded_at=now)
                s.add(result)
            result.home_score = f.full_time.home
            result.away_score = f.full_time.away
            result.home_score_ht = f.half_time.home if f.half_time else None
            result.away_score_ht = f.half_time.away if f.half_time else None
            result.recorded_at = now

    return len(batch.fixtures)
