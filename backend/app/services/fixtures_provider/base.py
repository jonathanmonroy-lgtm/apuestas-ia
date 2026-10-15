"""Common contract for fixtures/results providers.

The rest of the system only sees these DTOs, never a provider's JSON. Switching
providers = writing a new client + `normalize()`; ingestion, DB and API don't change.
"""

from datetime import date
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, Field

from app.models.enums import EventStatus, Provider, Sport
from app.services.http import RawResponse


class TeamDTO(BaseModel):
    external_id: str
    name: str
    short_name: str | None = None


class ScoreDTO(BaseModel):
    home: int = Field(ge=0)
    away: int = Field(ge=0)


class FixtureDTO(BaseModel):
    external_id: str
    starts_at: AwareDatetime
    # False when the provider only knows the date and starts_at's time is a placeholder.
    kickoff_confirmed: bool = False
    status: EventStatus
    round: str | None = None
    venue: str | None = None
    home: TeamDTO
    away: TeamDTO
    full_time: ScoreDTO | None = None
    half_time: ScoreDTO | None = None


class CompetitionDTO(BaseModel):
    external_id: str
    name: str
    sport: Sport
    country_code: str | None = None  # ISO 3166-1 alpha-2
    country_name: str | None = None  # provider's English name ("Colombia", "England", "World")


class SeasonDTO(BaseModel):
    external_id: str
    name: str  # e.g. "2026-27"
    start_date: date | None = None
    end_date: date | None = None


class FixturesBatch(BaseModel):
    """Everything one fixtures response contains, normalized."""

    source: Provider
    competition: CompetitionDTO
    season: SeasonDTO
    fixtures: list[FixtureDTO]


class FixturesProvider(Protocol):
    source: Provider

    def fetch_fixtures(
        self,
        competition: str,
        *,
        season: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> RawResponse:
        """Call the provider. Returns the raw JSON; raises a ProviderError subclass."""
        ...

    def normalize(self, raw: RawResponse) -> FixturesBatch:
        """Pure: raw JSON -> DTOs. Raises FormatError if the payload is unexpected."""
        ...

    def quota_remaining(self, raw: RawResponse) -> int | None:
        """Provider-reported remaining quota from response headers, if available."""
        ...

    def close(self) -> None: ...


def season_name(start: date | None, end: date | None, fallback_year: int | None = None) -> str:
    """'2026-27' for split seasons, '2026' for calendar-year ones."""
    if start and end and start.year != end.year:
        return f"{start.year}-{str(end.year)[-2:]}"
    if start:
        return str(start.year)
    return str(fallback_year) if fallback_year else "unknown"
