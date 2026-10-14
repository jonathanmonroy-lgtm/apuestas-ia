"""Import every model so Base.metadata is complete (Alembic autogenerate relies on it)."""

from app.models.bets import Bet
from app.models.events import Event, EventResult
from app.models.forecasts import Forecast, ForecastOutcome, Report
from app.models.ingestion import IngestionRun, ProviderQuota
from app.models.news import NewsItem, news_item_participant
from app.models.odds import OddSnapshot, UnmatchedOdd
from app.models.reference import Competition, ExternalRef, Participant, Season
from app.models.users import SelfExclusion, SpendingLimit, User

__all__ = [
    "Bet",
    "Competition",
    "Event",
    "EventResult",
    "ExternalRef",
    "Forecast",
    "ForecastOutcome",
    "IngestionRun",
    "NewsItem",
    "OddSnapshot",
    "Participant",
    "ProviderQuota",
    "Report",
    "Season",
    "SelfExclusion",
    "SpendingLimit",
    "UnmatchedOdd",
    "User",
    "news_item_participant",
]
