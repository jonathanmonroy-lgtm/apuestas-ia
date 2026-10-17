"""Calendar + odds endpoints.

GET endpoints serve ONLY what's in the database (ingestion lives in the worker), so a
provider outage can't break the pages. The one exception is POST /events/{id}/fetch-odds:
an explicit user action ("get the real odds of this match") that makes ONE budgeted
provider request for ONE match.

Response layers, kept apart so the UI can label them (see CLAUDE.md principles):
- `kickoff`, `result`, team names (data): what the calendar provider says.
- `odds` (kind="analysis"): a statistic OVER observed prices (median of N bookmakers,
  pre-match quotes only, exchanges excluded). No single bookmaker offers that number.
- `implied_probability` (kind="analysis"): arithmetic on those medians, not a forecast.
INTERNAL MODE: bookmaker names are never part of the response.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.jobs.sync_day import api_football
from app.models import Competition, Event, ExternalRef, IngestionRun, OddSnapshot
from app.models.enums import (
    EntityType,
    EventStatus,
    IngestionStatus,
    Market,
    Provider,
    QuotaPeriod,
)
from app.services.fixtures_provider import ProviderNotConfiguredError
from app.services.ingestion.day_job import JOB_DAY_FIXTURES, JOB_EVENT_ODDS, run_event_odds
from app.services.ingestion.espn_job import JOB_ESPN_DAY
from app.services.ingestion.fixtures_job import JOB_FIXTURES
from app.services.ingestion.runs import last_success_at
from app.services.odds_summary import Quote, implied_probability, summarize
from app.services.quota import QuotaPolicy

router = APIRouter(prefix="/api", tags=["events"])

MAX_RANGE_DAYS = 120
INTERNATIONAL = "World"  # "country" of international competitions (both day sources)
ODDS_METHOD = (
    "Mediana de la última cuota prepartido de cada casa (sin exchanges; se ignoran casas "
    "que llevan más de {hours:g} h sin actualizar). Agregado estadístico, no una cuota "
    "ofrecida por una casa concreta."
)
# API-Football gives ONE update time per match, not per bookmaker: say so, and don't
# claim a per-bookmaker freshness filter that cannot act on these data.
ODDS_METHOD_PER_MATCH_TIME = (
    "Mediana de las cuotas prepartido de las casas que ofrece el proveedor para este "
    "partido (sin exchanges). El proveedor da una sola hora de actualización por partido, "
    "no por casa. Agregado estadístico, no una cuota ofrecida por una casa concreta."
)
# ESPN shows ONE bookmaker's line: there is no median to speak of, and no quote time.
ODDS_METHOD_SINGLE_BOOK = (
    "Cuota prepartido de una sola casa (la que publica ESPN), pasada de formato americano "
    "a decimal (3 decimales). No es una mediana de mercado; la hora es cuando la leímos."
)
IMPLIED_METHOD = (
    "Inversa de la cuota mediana de cada resultado, normalizada para quitar el margen "
    "de las casas. Es lo que el mercado descuenta, no un pronóstico propio."
)


class TeamOut(BaseModel):
    id: int
    name: str


class ResultOut(BaseModel):
    home: int
    away: int


class Prices(BaseModel):
    home: Decimal
    draw: Decimal
    away: Decimal


class OddsOut(BaseModel):
    kind: Literal["analysis"] = "analysis"
    method: str
    market: Literal["h2h"] = "h2h"
    source: str
    median_price: Prices
    n_bookmakers: int
    as_of: datetime
    oldest_quote_at: datetime
    is_stale: bool


class ImpliedProbabilityOut(BaseModel):
    kind: Literal["analysis"] = "analysis"
    method: str = IMPLIED_METHOD
    home: Decimal
    draw: Decimal
    away: Decimal
    overround: Decimal


class EventOut(BaseModel):
    id: int
    competition_id: int
    competition: str
    country: str | None
    country_code: str | None
    round: str | None
    starts_at: datetime
    kickoff_confirmed: bool
    status: EventStatus
    home: TeamOut
    away: TeamOut
    result: ResultOut | None
    odds: OddsOut | None
    implied_probability: ImpliedProbabilityOut | None
    can_fetch_odds: bool  # an upcoming match from the multi-country source


class DataAsOf(BaseModel):
    fixtures: datetime | None  # last successful calendar sync (any calendar source)
    odds: datetime | None  # newest bookmaker quote actually stored (not just "a run ran")


class EventsResponse(BaseModel):
    data_as_of: DataAsOf
    events: list[EventOut]


class CountryOut(BaseModel):
    name: str  # provider's English name; "World" = international competitions
    code: str | None  # ISO alpha-2 when there is one (England has none)
    n_events: int
    n_with_odds: int
    leagues: list[str]


class CountriesResponse(BaseModel):
    day: date
    timezone: str
    source: str  # which multi-country source fills these views
    browse_days: list[date]  # the local days that source can have data for
    data_as_of: DataAsOf
    countries: list[CountryOut]


def utc_now() -> datetime:
    return datetime.now(UTC)


def prematch_cutoff(e: Event) -> datetime:
    """Until when a quote counts as pre-match. A date-only match (time to be confirmed,
    stored at 00:00 local) may be played any time that day: the whole day is pre-match."""
    return e.starts_at if e.kickoff_confirmed else e.starts_at + timedelta(days=1)


def local_day_bounds(day: date, offset_hours: int) -> tuple[datetime, datetime]:
    """Colombia's day (UTC-5) as a UTC interval: 2026-10-15 -> [10-15 05:00Z, 10-16 05:00Z)."""
    start = datetime.combine(day, time.min, UTC) - timedelta(hours=offset_hours)
    return start, start + timedelta(days=1)


def local_today(now: datetime, offset_hours: int) -> date:
    return (now + timedelta(hours=offset_hours)).date()


def tz_label(offset_hours: int) -> str:
    return f"UTC{offset_hours:+d}"


DAY_SOURCE_JOBS = {Provider.ESPN: JOB_ESPN_DAY, Provider.API_FOOTBALL: JOB_DAY_FIXTURES}


def day_source(settings: Settings) -> Provider:
    return Provider(settings.day_source)


def browse_days(settings: Settings, today: date) -> list[date]:
    if day_source(settings) is Provider.ESPN:
        first, n = (
            today - timedelta(days=settings.espn_days_back),
            (settings.espn_days_back + settings.espn_days_ahead + 1),
        )
    else:  # API-Football free plan: yesterday..tomorrow
        first, n = today - timedelta(days=1), 3
    return [first + timedelta(days=i) for i in range(n)]


def _hide_other_day_sources(settings: Settings):
    """Both multi-country sources list the same matches under different ids: only the
    active one's events are shown (the other's stay stored, e.g. after switching)."""
    hidden = [p for p in DAY_SOURCE_JOBS if p is not day_source(settings)]
    return Event.id.not_in(
        select(ExternalRef.entity_id).where(
            ExternalRef.source.in_(hidden), ExternalRef.entity_type == EntityType.EVENT
        )
    )


def _data_as_of(db: Session, settings: Settings) -> DataAsOf:
    source = day_source(settings)
    runs = [
        last_success_at(db, Provider(settings.fixtures_provider), JOB_FIXTURES),
        last_success_at(db, source, DAY_SOURCE_JOBS[source]),
    ]
    return DataAsOf(
        fixtures=max((r for r in runs if r), default=None),
        odds=db.scalar(select(func.max(OddSnapshot.captured_at))),
    )


def _load_events(db: Session, where: list) -> list[Event]:
    return list(
        db.scalars(
            select(Event)
            .options(
                joinedload(Event.home),
                joinedload(Event.away),
                joinedload(Event.competition),
                joinedload(Event.result),
            )
            .join(Event.competition)
            .where(*where)
            .order_by(Event.starts_at, Event.id)
        )
        .unique()
        .all()
    )


def _quotes(
    db: Session, events: list[Event], settings: Settings
) -> dict[int, tuple[Provider, list[Quote]]]:
    """Per event, quotes from ONE source: the dedicated odds provider when it has any,
    else the multi-country source. Two providers' books are never mixed in one median."""
    if not events:
        return {}
    preferred = Provider(settings.odds_provider)
    rows = db.execute(
        select(
            OddSnapshot.event_id,
            OddSnapshot.source,
            OddSnapshot.bookmaker,
            OddSnapshot.selection,
            OddSnapshot.price,
            OddSnapshot.captured_at,
        ).where(
            OddSnapshot.event_id.in_([e.id for e in events]),
            OddSnapshot.market == Market.H2H,
            OddSnapshot.source.in_([preferred, Provider.API_FOOTBALL, Provider.ESPN]),
        )
    )
    kickoff = {e.id: prematch_cutoff(e) for e in events}
    by_source: dict[int, dict[Provider, list[Quote]]] = {}
    for event_id, source, book, sel, price, captured_at in rows:
        if captured_at >= kickoff[event_id]:
            continue  # pre-match prices only (defence in depth; ingestion skips live)
        by_source.setdefault(event_id, {}).setdefault(source, []).append(
            Quote(book, sel, price, captured_at)
        )
    out = {}
    for event_id, sources in by_source.items():
        source = preferred if preferred in sources else next(iter(sources))
        out[event_id] = (source, sources[source])
    return out


@router.get("/events", response_model=EventsResponse)
def list_events(
    date_from: date | None = Query(None, description="UTC date; default today"),  # noqa: B008
    date_to: date | None = Query(None, description="UTC date, inclusive; default +14 days"),  # noqa: B008
    matchday: int | None = Query(
        None, ge=1, le=60, description="a whole matchday (jornada), past or future; ignores dates"
    ),
    day: date | None = Query(None, description="a whole LOCAL day (Colombia); ignores dates"),  # noqa: B008
    country: str | None = Query(None, description="provider country name, e.g. Colombia"),
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    now: datetime = Depends(utc_now),  # noqa: B008 - injectable clock for tests
) -> EventsResponse:
    if matchday is not None:
        # football-data labels LaLiga rounds "Jornada N" (football_data normalizer).
        where = [Event.round == f"Jornada {matchday}"]
    elif day is not None:
        start, end = local_day_bounds(day, settings.local_utc_offset_hours)
        where = [Event.starts_at >= start, Event.starts_at < end]
    else:
        start_d = date_from or now.date()
        end_d = date_to or start_d + timedelta(days=14)
        if end_d < start_d or (end_d - start_d).days > MAX_RANGE_DAYS:
            raise HTTPException(422, f"date range must be 0..{MAX_RANGE_DAYS} days")
        where = [
            Event.starts_at >= datetime.combine(start_d, time.min, UTC),
            Event.starts_at < datetime.combine(end_d + timedelta(days=1), time.min, UTC),
        ]
    if country == INTERNATIONAL:
        # The menu files competitions without a country under "World" too.
        where.append(
            or_(Competition.country_name == INTERNATIONAL, Competition.country_name.is_(None))
        )
    elif country is not None:
        where.append(Competition.country_name == country)
    where.append(_hide_other_day_sources(settings))

    events = _load_events(db, where)
    quotes = _quotes(db, events, settings)
    api_football_events = _api_football_event_ids(db, events)
    stale_after = timedelta(hours=settings.odds_stale_after_hours)
    return EventsResponse(
        data_as_of=_data_as_of(db, settings),
        events=[
            _event_out(e, quotes.get(e.id), stale_after, now, e.id in api_football_events)
            for e in events
        ],
    )


@router.get("/countries", response_model=CountriesResponse)
def list_countries(
    day: date | None = Query(None, description="LOCAL day (Colombia); default today"),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    now: datetime = Depends(utc_now),  # noqa: B008
) -> CountriesResponse:
    day = day or local_today(now, settings.local_utc_offset_hours)
    start, end = local_day_bounds(day, settings.local_utc_offset_hours)
    events = _load_events(
        db, [Event.starts_at >= start, Event.starts_at < end, _hide_other_day_sources(settings)]
    )
    quotes = _quotes(db, events, settings)
    stale_after = timedelta(hours=settings.odds_stale_after_hours)
    # "with odds" = what the page will actually show (a summary), not "any snapshot":
    # an exchange-only or incomplete 1X2 match shows no odds, so it must not count.
    shown = {eid for eid, (_, q) in quotes.items() if summarize(q, stale_after) is not None}
    stats: dict[str, dict] = {}
    for e in events:
        name = e.competition.country_name or INTERNATIONAL
        c = stats.setdefault(
            name, {"code": e.competition.country_code, "n": 0, "odds": 0, "leagues": set()}
        )
        c["n"] += 1
        c["odds"] += e.id in shown
        c["leagues"].add(e.competition.name)
    countries = [
        CountryOut(name=n, code=c["code"], n_events=c["n"], n_with_odds=c["odds"],
                   leagues=sorted(c["leagues"]))
        for n, c in stats.items()
    ]  # fmt: skip
    countries.sort(key=lambda c: (c.name == INTERNATIONAL, c.name))
    return CountriesResponse(
        day=day,
        timezone=tz_label(settings.local_utc_offset_hours),
        source=day_source(settings).value,
        browse_days=browse_days(settings, local_today(now, settings.local_utc_offset_hours)),
        data_as_of=_data_as_of(db, settings),
        countries=countries,
    )


class FetchOddsOut(BaseModel):
    status: str
    snapshots_inserted: int
    message: str


@router.post("/events/{event_id}/fetch-odds", response_model=FetchOddsOut)
def fetch_event_odds(
    event_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    now: datetime = Depends(utc_now),  # noqa: B008
) -> FetchOddsOut:
    """Explicit user action: ONE budgeted request for this match's real pre-match odds."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "event not found")
    if now >= prematch_cutoff(event) or event.status is not EventStatus.SCHEDULED:
        raise HTTPException(
            409, "el partido ya empezó o no está programado: sólo hay cuotas prepartido"
        )
    fixture_id = _fixture_external_id(db, event_id)
    if fixture_id is None:
        raise HTTPException(422, "este partido no viene de la fuente multi-país")
    # Look in the database BEFORE spending a request (QA #1). Usage limits are measured
    # on the wall clock: run records are stamped with it (`now` may be a test clock).
    wall = datetime.now(UTC)
    recent = _last_event_odds_run(db, fixture_id)
    if recent and wall - recent < timedelta(minutes=settings.on_demand_odds_cooldown_minutes):
        minutes = int((wall - recent).total_seconds() // 60)
        return FetchOddsOut(
            status="recent",
            snapshots_inserted=0,
            message=f"Ya se consultó hace {minutes} min; se mostrará lo que haya guardado.",
        )
    if _calls_since(db, wall - timedelta(minutes=1)) >= settings.api_football_per_minute_cap:
        return FetchOddsOut(
            status="rate_limited",
            snapshots_inserted=0,
            message=(
                "Demasiadas consultas en este minuto (límite del plan gratuito); "
                "prueba en un minuto."
            ),
        )
    day_start = datetime.combine(wall.date(), time.min, UTC)
    if _calls_since(db, day_start, JOB_EVENT_ODDS) >= settings.api_football_on_demand_daily_cap:
        return FetchOddsOut(
            status="no_budget",
            snapshots_inserted=0,
            message="Se agotaron las consultas de cuotas de hoy (plan gratuito).",
        )
    try:
        provider = api_football(settings)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        # Same database as this request, in the short transactions the job expects.
        session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
        outcome = run_event_odds(
            session_factory, provider, event_id,
            policy=QuotaPolicy(QuotaPeriod.DAY, settings.api_football_daily_budget),
        )  # fmt: skip
    finally:
        provider.close()
    if outcome is None:  # the ref vanished between the check and the call
        raise HTTPException(422, "este partido no viene de la fuente multi-país")
    if outcome.status is IngestionStatus.SKIPPED_NO_BUDGET:
        return FetchOddsOut(
            status="no_budget",
            snapshots_inserted=0,
            message="Se agotaron las consultas de cuotas de hoy (plan gratuito).",
        )
    if outcome.status is not IngestionStatus.SUCCESS:
        return FetchOddsOut(
            status="error",
            snapshots_inserted=0,
            message=f"El proveedor no respondió bien: {outcome.error}",
        )
    return FetchOddsOut(
        status="ok",
        snapshots_inserted=outcome.records_written,
        message="Cuotas actualizadas." if outcome.records_written else
        "El proveedor no tiene cuotas para este partido.",
    )  # fmt: skip


def _fixture_external_id(db: Session, event_id: int) -> str | None:
    return db.scalar(
        select(ExternalRef.external_id).where(
            ExternalRef.source == Provider.API_FOOTBALL,
            ExternalRef.entity_type == EntityType.EVENT,
            ExternalRef.entity_id == event_id,
        )
    )


def _last_event_odds_run(db: Session, fixture_id: str) -> datetime | None:
    """When this match's odds were last asked for successfully (even if it had none)."""
    return db.scalar(
        select(func.max(IngestionRun.finished_at)).where(
            IngestionRun.source == Provider.API_FOOTBALL,
            IngestionRun.job == JOB_EVENT_ODDS,
            IngestionRun.status == IngestionStatus.SUCCESS,
            IngestionRun.request_url.like(f"%fixture={fixture_id}%"),
        )
    )


def _calls_since(db: Session, since: datetime, job: str | None = None) -> int:
    query = (
        select(func.count())
        .select_from(IngestionRun)
        .where(
            IngestionRun.source == Provider.API_FOOTBALL,
            IngestionRun.started_at >= since,
            IngestionRun.status != IngestionStatus.SKIPPED_NO_BUDGET,
        )
    )
    if job is not None:
        query = query.where(IngestionRun.job == job)
    return db.scalar(query) or 0


def _api_football_event_ids(db: Session, events: list[Event]) -> set[int]:
    if not events:
        return set()
    return set(
        db.scalars(
            select(ExternalRef.entity_id).where(
                ExternalRef.source == Provider.API_FOOTBALL,
                ExternalRef.entity_type == EntityType.EVENT,
                ExternalRef.entity_id.in_([e.id for e in events]),
            )
        )
    )


def _event_out(
    e: Event,
    picked: tuple[Provider, list[Quote]] | None,
    stale_after: timedelta,
    now: datetime,
    from_api_football: bool,
) -> EventOut:
    summary = summarize(picked[1], stale_after) if picked else None
    odds = implied = None
    if summary is not None:
        prices = {s.value: p for s, p in summary.median_price.items()}
        if picked[0] is Provider.ESPN:
            method = ODDS_METHOD_SINGLE_BOOK
        elif picked[0] is Provider.API_FOOTBALL:
            method = ODDS_METHOD_PER_MATCH_TIME
        else:
            method = ODDS_METHOD.format(hours=stale_after.total_seconds() / 3600)
        odds = OddsOut(
            method=method,
            source=picked[0].value,
            median_price=Prices(**prices),
            n_bookmakers=summary.n_bookmakers,
            as_of=summary.as_of,
            oldest_quote_at=summary.oldest_quote_at,
            is_stale=now - summary.as_of > stale_after,
        )
        ip = implied_probability(summary.median_price)
        implied = ImpliedProbabilityOut(
            **{s.value: p for s, p in ip.probability.items()}, overround=ip.overround
        )
    return EventOut(
        id=e.id,
        competition_id=e.competition_id,
        competition=e.competition.name,
        country=e.competition.country_name,
        country_code=e.competition.country_code,
        round=e.round,
        starts_at=e.starts_at,
        kickoff_confirmed=e.kickoff_confirmed,
        status=e.status,
        home=TeamOut(id=e.home.id, name=e.home.name),
        away=TeamOut(id=e.away.id, name=e.away.name),
        result=ResultOut(home=e.result.home_score, away=e.result.away_score) if e.result else None,
        odds=odds,
        implied_probability=implied,
        can_fetch_odds=from_api_football
        and e.status is EventStatus.SCHEDULED
        and now < prematch_cutoff(e),
    )
