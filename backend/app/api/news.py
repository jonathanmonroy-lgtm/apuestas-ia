"""Read-only news endpoint. Serves only what the worker stored; never calls NewsAPI.

News are third-party content (kind="news"): neither our data nor our analysis. Only a
short summary is served, with the publisher and a link to the original article.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Event, NewsItem
from app.models.enums import Provider
from app.models.news import news_item_participant
from app.services.ingestion.news_job import JOB_NEWS
from app.services.ingestion.runs import last_success_at

router = APIRouter(prefix="/api", tags=["news"])


class TeamOut(BaseModel):
    id: int
    name: str


class NewsOut(BaseModel):
    id: int
    kind: Literal["news"] = "news"
    title: str
    summary: str | None
    url: str
    publisher: str | None
    published_at: datetime
    teams: list[TeamOut]
    # The article is third-party data; WHICH teams it is about is our inference.
    teams_assignment: Literal["inferred_by_rules"] = "inferred_by_rules"


class NewsDataAsOf(BaseModel):
    checked_at: datetime | None  # last successful sync
    newest_article_at: datetime | None
    provider_delay_hours: int  # free plan delivers articles ~24 h late


class NewsResponse(BaseModel):
    data_as_of: NewsDataAsOf
    notice: str
    items: list[NewsOut]


@router.get("/news", response_model=NewsResponse)
def list_news(
    participant_id: int | None = Query(None, description="only news about this team"),
    event_id: int | None = Query(None, description="news about either team of this match"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> NewsResponse:
    team_ids: list[int] | None = None
    if event_id is not None:
        event = db.get(Event, event_id)
        if event is None:
            raise HTTPException(404, "event not found")
        team_ids = [event.home_participant_id, event.away_participant_id]
    if participant_id is not None:
        team_ids = (
            [participant_id] if team_ids is None else [t for t in team_ids if t == participant_id]
        )

    query = select(NewsItem).options(selectinload(NewsItem.participants))
    if team_ids is not None:
        query = query.where(
            NewsItem.id.in_(
                select(news_item_participant.c.news_item_id).where(
                    news_item_participant.c.participant_id.in_(team_ids)
                )
            )
        )
    items = db.scalars(query.order_by(NewsItem.published_at.desc()).limit(limit)).all()

    delay = settings.news_provider_delay_hours
    return NewsResponse(
        data_as_of=NewsDataAsOf(
            checked_at=last_success_at(db, Provider(settings.news_provider), JOB_NEWS),
            newest_article_at=db.scalar(select(func.max(NewsItem.published_at))),
            provider_delay_hours=delay,
        ),
        notice=(
            f"Noticias de terceros con ~{delay} h de retraso (plan gratuito de NewsAPI). "
            "Asignadas a equipos por reglas automáticas: puede haber errores."
        ),
        items=[
            NewsOut(
                id=n.id,
                title=n.title,
                summary=n.summary,
                url=n.url,
                publisher=n.publisher,
                published_at=n.published_at,
                teams=[TeamOut(id=p.id, name=p.name) for p in n.participants],
            )
            for n in items
        ],
    )
