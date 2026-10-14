"""Common contract for odds providers. The rest of the system only sees these DTOs."""

from decimal import Decimal
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, Field

from app.models.enums import Market, Provider, Selection
from app.services.http import RawResponse


class PriceDTO(BaseModel):
    bookmaker: str  # provider's bookmaker key; stored, never exposed in INTERNAL MODE
    market: Market
    selection: Selection
    price: Decimal = Field(gt=1)  # decimal odds, exact
    captured_at: AwareDatetime  # bookmaker's last update according to the provider


class OddsEventDTO(BaseModel):
    """An event as the odds provider sees it. Never becomes an Event by itself."""

    external_id: str
    commence_time: AwareDatetime
    home_name: str
    away_name: str
    prices: list[PriceDTO]


class OddsBatch(BaseModel):
    source: Provider
    competition: str  # our internal competition code, e.g. "PD"
    events: list[OddsEventDTO]


class OddsProvider(Protocol):
    source: Provider

    def fetch_odds(self, competition: str) -> RawResponse:
        """Pre-match odds only: events not started yet, quotes from before kick-off."""
        ...

    def expected_cost(self) -> int: ...

    def normalize(self, raw: RawResponse, competition: str) -> OddsBatch: ...

    def request_cost(self, raw: RawResponse) -> int:
        """Credits this call consumed (provider-reported when available)."""
        ...

    def quota_remaining(self, raw: RawResponse) -> int | None: ...

    def close(self) -> None: ...
