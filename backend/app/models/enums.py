from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def db_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Portable enum column: VARCHAR + CHECK constraint (no native PG enum).

    Native enums make migrations painful (ALTER TYPE); a CHECK is enough here.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Sport(StrEnum):
    FOOTBALL = "football"
    BASKETBALL = "basketball"


class EventStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class EntityType(StrEnum):
    COMPETITION = "competition"
    SEASON = "season"
    PARTICIPANT = "participant"
    EVENT = "event"


class Market(StrEnum):
    # MVP: only 1X2. Add OVER_UNDER etc. when approved (doubles Odds API credit cost).
    H2H = "h2h"


class Selection(StrEnum):
    HOME = "home"
    DRAW = "draw"
    AWAY = "away"


class BetStatus(StrEnum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"


class LimitPeriod(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class Provider(StrEnum):
    API_FOOTBALL = "api_football"
    FOOTBALL_DATA = "football_data"
    THE_ODDS_API = "the_odds_api"
    NEWSAPI = "newsapi"
    ESPN = "espn"


class IngestionStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"  # unexpected format / unknown error
    QUOTA_EXHAUSTED = "quota_exhausted"
    AUTH_ERROR = "auth_error"
    THROTTLED = "throttled"  # gave up after retries
    SKIPPED_NO_BUDGET = "skipped_no_budget"  # QuotaBudget refused before calling


class QuotaPeriod(StrEnum):
    DAY = "day"
    MONTH = "month"


class ReportStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    REJECTED = "rejected"  # failed numeric validation against the Forecast
