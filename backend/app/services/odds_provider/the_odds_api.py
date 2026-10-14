"""The Odds API v4 adapter (https://the-odds-api.com).

Free plan: 500 credits/month. Cost of /odds = number of regions x number of markets
(1 for regions=eu, markets=h2h), reported per call in `x-requests-last`; the remaining
balance comes in `x-requests-remaining`. The key can only travel as the `apiKey` query
param, so it is passed as `auth_params` and stripped from every stored URL.

PRE-MATCH ONLY: /odds also returns events already in play, with live prices (1.10 for a
team 2-0 up). Mixing those with pre-match prices would poison both the summary and the
model's history, so we ask only for future kick-offs (commenceTimeFrom) and, defensively,
drop any event that has started and any quote stamped at or after kick-off.

Verified 2026-10-14 with a real call (LaLiga, regions=eu, markets=h2h): 21 events,
21 bookmakers each. Betfair-style exchanges also return an `h2h_lay` market (prices to
bet AGAINST an outcome): it is not a back price and is discarded.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import AwareDatetime, BaseModel, ValidationError

from app.core.logging import get_logger
from app.models.enums import Market, Provider, Selection
from app.services.errors import (
    AuthError,
    FormatError,
    ProviderError,
    QuotaExhaustedError,
)
from app.services.http import ProviderHttpClient, RawResponse
from app.services.odds_provider.base import OddsBatch, OddsEventDTO, PriceDTO

log = get_logger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEYS = {"PD": "soccer_spain_la_liga"}  # our competition code -> provider sport key
MARKETS = {"h2h": Market.H2H}  # only back markets we ingest; h2h_lay is NOT here
DRAW_NAME = "Draw"


def classify_body(response: httpx.Response, payload: Any) -> ProviderError | None:
    """Errors come as {"message", "error_code", "details_url"} with 401/429/422."""
    if not isinstance(payload, dict) or "error_code" not in payload:
        return None
    code = str(payload["error_code"])
    message = f"{code}: {payload.get('message')}"
    status = response.status_code
    if "CREDITS" in code or "QUOTA" in code:  # OUT_OF_USAGE_CREDITS
        return QuotaExhaustedError(message, http_status=status)
    if code == "EXCEEDED_FREQ_LIMIT":
        return None  # per-second limit: let the HTTP layer back off and retry
    if status in (401, 403) or "KEY" in code:  # INVALID_KEY, MISSING_KEY...
        return AuthError(message, http_status=status)
    return ProviderError(message, http_status=status)


# --- Minimal schema of what we rely on (extra fields ignored) ---
class _Outcome(BaseModel):
    name: str
    price: float


class _Market(BaseModel):
    key: str
    last_update: AwareDatetime | None = None
    outcomes: list[_Outcome]


class _Bookmaker(BaseModel):
    key: str
    last_update: AwareDatetime
    markets: list[_Market]


class _Event(BaseModel):
    id: str
    commence_time: AwareDatetime
    home_team: str
    away_team: str
    bookmakers: list[_Bookmaker]


def to_decimal(price: float) -> Decimal:
    """JSON gives a float. str() is the shortest repr that round-trips, so 2.12 becomes
    Decimal('2.12') exactly as the provider wrote it (never Decimal(2.12), which would
    expand the binary error)."""
    try:
        return Decimal(str(price))
    except InvalidOperation as exc:
        raise FormatError(f"invalid price {price!r}") from exc


class TheOddsApiProvider:
    source = Provider.THE_ODDS_API

    def __init__(
        self, api_key: str, *, regions: str = "eu", client: ProviderHttpClient | None = None
    ) -> None:
        self.regions = regions
        self._http = client or ProviderHttpClient(
            source=self.source.value,
            base_url=BASE_URL,
            auth_params={"apiKey": api_key},
            classify_body=classify_body,
        )

    def close(self) -> None:
        self._http.close()

    def fetch_odds(self, competition: str, now: datetime | None = None) -> RawResponse:
        if competition not in SPORT_KEYS:
            raise ProviderError(f"unknown competition code for The Odds API: {competition}")
        now = now or datetime.now(UTC)
        return self._http.get_json(
            f"/sports/{SPORT_KEYS[competition]}/odds",
            params={
                "regions": self.regions,
                "markets": ",".join(MARKETS),
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "commenceTimeFrom": now.strftime("%Y-%m-%dT%H:%M:%SZ"),  # pre-match only
            },
        )

    def expected_cost(self) -> int:
        """Documented cost formula: regions x markets (checked before calling)."""
        return len([r for r in self.regions.split(",") if r.strip()]) * len(MARKETS)

    def request_cost(self, raw: RawResponse) -> int:
        value = raw.headers.get("x-requests-last")
        if value is not None and value.isdigit():
            return int(value)
        return self.expected_cost()

    def quota_remaining(self, raw: RawResponse) -> int | None:
        value = raw.headers.get("x-requests-remaining")
        try:
            return int(float(value)) if value is not None else None
        except ValueError:
            return None

    def normalize(self, raw: RawResponse, competition: str) -> OddsBatch:
        if not isinstance(raw.payload, list):
            raise FormatError("The Odds API: expected a list of events")
        try:
            events = [_Event.model_validate(e) for e in raw.payload]
        except ValidationError as exc:
            raise FormatError(f"unexpected odds payload: {exc.error_count()} errors") from exc
        upcoming = []
        for e in events:
            if e.commence_time <= raw.fetched_at:
                log.warning("odds_event_already_started_skipped", odds_event_id=e.id)
                continue
            upcoming.append(e)
        try:
            return OddsBatch(
                source=self.source,
                competition=competition,
                events=[self._event(e) for e in upcoming],
            )
        except ValidationError as exc:
            raise FormatError(f"unexpected odds data: {exc}") from exc

    @staticmethod
    def _event(e: _Event) -> OddsEventDTO:
        names = {
            e.home_team: Selection.HOME,
            e.away_team: Selection.AWAY,
            DRAW_NAME: Selection.DRAW,
        }
        prices: list[PriceDTO] = []
        for book in e.bookmakers:
            for market in book.markets:
                if market.key not in MARKETS:
                    continue  # h2h_lay and anything we don't ingest
                selections = {o.name for o in market.outcomes}
                if selections != set(names):
                    # Not a clean 1X2 (unknown outcome names): drop this bookmaker's market
                    # rather than guess which price is which.
                    log.warning(
                        "odds_unexpected_outcomes",
                        odds_event_id=e.id, bookmaker=book.key, outcomes=sorted(selections),
                    )  # fmt: skip
                    continue
                captured_at = market.last_update or book.last_update
                if captured_at >= e.commence_time:
                    continue  # an in-play quote, never mixed with pre-match prices
                try:
                    quotes = [
                        PriceDTO(
                            bookmaker=book.key,
                            market=MARKETS[market.key],
                            selection=names[o.name],
                            price=to_decimal(o.price),
                            captured_at=captured_at,
                        )
                        for o in market.outcomes
                    ]
                except (ValidationError, FormatError):
                    # e.g. a suspended market quoted at 1.0: drop this bookmaker only,
                    # never the whole run (the credit is already spent).
                    log.warning(
                        "odds_invalid_price_skipped", odds_event_id=e.id, bookmaker=book.key
                    )
                    continue
                prices.extend(quotes)
        return OddsEventDTO(
            external_id=e.id,
            commence_time=e.commence_time,
            home_name=e.home_team,
            away_name=e.away_team,
            prices=prices,
        )
