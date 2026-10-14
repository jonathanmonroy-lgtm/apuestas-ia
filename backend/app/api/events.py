"""Read-only calendar + odds endpoint. Serves ONLY what's in the database: it never calls a
provider (ingestion lives in the worker), so a provider outage can't break the API.

Response layers, kept apart so the UI can label them (see CLAUDE.md principles):
- `odds` (kind="data"): median of observed bookmaker prices + when they were observed.
- `implied_probability` (kind="analysis"): arithmetic on those prices, not a forecast.
INTERNAL MODE: bookmaker names are never part of the response.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Event, OddSnapshot
from app.models.enums import EventStatus, Market, Provider
from app.services.ingestion.fixtures_job import JOB_FIXTURES
from app.services.ingestion.odds_job import JOB_ODDS
from app.services.ingestion.runs import last_success_at
from app.services.odds_summary import Quote, implied_probability, summarize

router = APIRouter(prefix="/api", tags=["events"])

MAX_RANGE_DAYS = 120
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
    kind: Literal["data"] = "data"
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
    competition: str
    round: str | None
    starts_at: datetime
    kickoff_confirmed: bool
    status: EventStatus
    home: TeamOut
    away: TeamOut
    result: ResultOut | None
    odds: OddsOut | None
    implied_probability: ImpliedProbabilityOut | None


class DataAsOf(BaseModel):
    fixtures: datetime | None
    odds: datetime | None


class EventsResponse(BaseModel):
    data_as_of: DataAsOf
    events: list[EventOut]


@router.get("/events", response_model=EventsResponse)
def list_events(
    date_from: date | None = Query(None, description="UTC date; default today"),  # noqa: B008
    date_to: date | None = Query(None, description="UTC date, inclusive; default +14 days"),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> EventsResponse:
    now = datetime.now(UTC)
    start = date_from or now.date()
    end = date_to or start + timedelta(days=14)
    if end < start or (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"date range must be 0..{MAX_RANGE_DAYS} days")

    events = (
        db.scalars(
            select(Event)
            .options(
                joinedload(Event.home),
                joinedload(Event.away),
                joinedload(Event.competition),
                joinedload(Event.result),
            )
            .where(
                Event.starts_at >= datetime.combine(start, time.min, UTC),
                Event.starts_at < datetime.combine(end + timedelta(days=1), time.min, UTC),
            )
            .order_by(Event.starts_at, Event.id)
        )
        .unique()
        .all()
    )

    quotes: dict[int, list[Quote]] = {}
    if events:
        rows = db.execute(
            select(
                OddSnapshot.event_id,
                OddSnapshot.bookmaker,
                OddSnapshot.selection,
                OddSnapshot.price,
                OddSnapshot.captured_at,
            ).where(
                OddSnapshot.event_id.in_([e.id for e in events]),
                OddSnapshot.market == Market.H2H,
            )
        )
        for event_id, book, sel, price, captured_at in rows:
            quotes.setdefault(event_id, []).append(Quote(book, sel, price, captured_at))

    stale_after = timedelta(hours=settings.odds_stale_after_hours)
    return EventsResponse(
        data_as_of=DataAsOf(
            fixtures=last_success_at(db, Provider(settings.fixtures_provider), JOB_FIXTURES),
            odds=last_success_at(db, Provider(settings.odds_provider), JOB_ODDS),
        ),
        events=[_event_out(e, quotes.get(e.id, []), stale_after, now) for e in events],
    )


def _event_out(e: Event, quotes: list[Quote], stale_after: timedelta, now: datetime) -> EventOut:
    summary = summarize(quotes, stale_after)
    odds = implied = None
    if summary is not None:
        prices = {s.value: p for s, p in summary.median_price.items()}
        odds = OddsOut(
            source=Provider.THE_ODDS_API.value,
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
        competition=e.competition.name,
        round=e.round,
        starts_at=e.starts_at,
        kickoff_confirmed=e.kickoff_confirmed,
        status=e.status,
        home=TeamOut(id=e.home.id, name=e.home.name),
        away=TeamOut(id=e.away.id, name=e.away.name),
        result=ResultOut(home=e.result.home_score, away=e.result.away_score) if e.result else None,
        odds=odds,
        implied_probability=implied,
    )
