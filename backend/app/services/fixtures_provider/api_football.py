"""API-Football (api-sports.io) adapter: the multi-country source for "today's matches by
country" and their pre-match odds.

Free plan, verified 2026-10-14/15 with real calls:
- Any league by SEASON is refused ("Free plans do not have access to this season, try
  from 2022 to 2024"), but fixtures BY DATE work for yesterday/today/tomorrow across all
  countries (154 matches in 39 countries on 2026-10-15, Colombia included).
- /odds?date= is capped at page 3 (~30 matches); /odds?fixture=<id> works for any match
  in the window (9 bookmakers on the matches tried) -> odds on demand per match.
- 100 requests/day, 10/minute. Errors come inside HTTP 200 bodies (classify_body).
Dates are requested in Colombia's time zone, so "today" is Colombia's day.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import AwareDatetime, BaseModel, ValidationError

from app.core.logging import get_logger
from app.models.enums import EventStatus, Market, Provider, Selection, Sport
from app.services.errors import (
    AuthError,
    FormatError,
    ProviderError,
    QuotaExhaustedError,
    ThrottledError,
)
from app.services.fixtures_provider.base import (
    CompetitionDTO,
    FixtureDTO,
    FixturesBatch,
    ScoreDTO,
    SeasonDTO,
    TeamDTO,
)
from app.services.http import ProviderHttpClient, RawResponse
from app.services.odds_provider.base import PriceDTO

log = get_logger(__name__)

BASE_URL = "https://v3.football.api-sports.io"
LEAGUE_IDS = {"PD": 140}  # our competition code -> API-Football league id
REQUEST_TZ = "America/Bogota"  # the product's "day" (Colombia, UTC-5, no DST)
FREE_ODDS_MAX_PAGE = 3

# status -> (our status, is the kick-off time real?)
STATUS_MAP: dict[str, tuple[EventStatus, bool]] = {
    "TBD": (EventStatus.SCHEDULED, False),  # time to be defined: date only
    "NS": (EventStatus.SCHEDULED, True),
    "1H": (EventStatus.LIVE, True),
    "HT": (EventStatus.LIVE, True),
    "2H": (EventStatus.LIVE, True),
    "ET": (EventStatus.LIVE, True),
    "BT": (EventStatus.LIVE, True),
    "P": (EventStatus.LIVE, True),
    "LIVE": (EventStatus.LIVE, True),
    "SUSP": (EventStatus.POSTPONED, False),
    "INT": (EventStatus.LIVE, True),  # interrupted mid-match: still in play, not postponed
    "PST": (EventStatus.POSTPONED, False),
    "FT": (EventStatus.FINISHED, True),
    "AET": (EventStatus.FINISHED, True),
    "PEN": (EventStatus.FINISHED, True),
    "CANC": (EventStatus.CANCELLED, False),
    "ABD": (EventStatus.CANCELLED, False),
    # Administrative results (walkover, awarded): finished, no played score; the kick-off
    # time itself is known (it is not "to be confirmed").
    "AWD": (EventStatus.FINISHED, True),
    "WO": (EventStatus.FINISHED, True),
}
PLAYED_STATUSES = {"FT", "AET", "PEN"}
OUTCOMES = {"Home": Selection.HOME, "Draw": Selection.DRAW, "Away": Selection.AWAY}


def classify_body(response: httpx.Response, payload: Any) -> ProviderError | None:
    """API-Sports returns HTTP 200 with `errors` populated (dict, or [] when no errors)."""
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if not errors:
        return None
    if isinstance(errors, list):
        errors = {str(i): e for i, e in enumerate(errors)}
    message = "; ".join(f"{k}: {v}" for k, v in errors.items())
    if "requests" in errors:  # daily quota
        return QuotaExhaustedError(message, http_status=response.status_code)
    if "rateLimit" in errors:  # per-minute limit: transient, retry later
        return ThrottledError(message, http_status=response.status_code)
    if "token" in errors or "plan" in errors or "access" in errors:
        return AuthError(message, http_status=response.status_code)
    return ProviderError(message, http_status=response.status_code)


# --- minimal schema of what we rely on (extra fields ignored) ---
class _Status(BaseModel):
    short: str


class _Venue(BaseModel):
    name: str | None = None


class _Fixture(BaseModel):
    id: int
    date: AwareDatetime
    status: _Status
    venue: _Venue | None = None


class _League(BaseModel):
    id: int
    name: str
    country: str | None = None
    flag: str | None = None
    season: int
    round: str | None = None


class _Team(BaseModel):
    id: int
    name: str


class _Teams(BaseModel):
    home: _Team
    away: _Team


class _Half(BaseModel):
    home: int | None = None
    away: int | None = None


class _Score(BaseModel):
    halftime: _Half
    fulltime: _Half  # the 90-minute score; extra time and penalties come apart


class _Item(BaseModel):
    fixture: _Fixture
    league: _League
    teams: _Teams
    score: _Score


class _OddValue(BaseModel):
    value: str
    odd: str


class _Bet(BaseModel):
    name: str
    # Other markets carry numeric values ("Exact Goals Number": 0, 1, 2...): only the
    # Match Winner market is validated, when we read it (verified on a real response).
    values: list[dict[str, Any]]


class _Bookmaker(BaseModel):
    name: str
    bets: list[_Bet]


class _OddsFixture(BaseModel):
    id: int


class _OddsItem(BaseModel):
    fixture: _OddsFixture
    update: AwareDatetime
    bookmakers: list[_Bookmaker]


class FixtureOdds(BaseModel):
    """Pre-match 1X2 prices of one fixture, keyed by the provider's fixture id."""

    fixture_external_id: str
    prices: list[PriceDTO]


def country_code_from_flag(flag: str | None) -> str | None:
    """'https://media.api-sports.io/flags/co.svg' -> 'CO'. Subdivisions such as gb-eng
    (England) have no ISO 3166-1 alpha-2 code: None (the name still identifies them)."""
    if not flag:
        return None
    code = flag.rsplit("/", 1)[-1].removesuffix(".svg")
    return code.upper() if len(code) == 2 and code.isalpha() else None


class ApiFootballProvider:
    source = Provider.API_FOOTBALL

    def __init__(self, api_key: str, *, client: ProviderHttpClient | None = None) -> None:
        self._http = client or ProviderHttpClient(
            source=self.source.value,
            base_url=BASE_URL,
            auth_headers={"x-apisports-key": api_key},
            classify_body=classify_body,
        )

    def close(self) -> None:
        self._http.close()

    def quota_remaining(self, raw: RawResponse) -> int | None:
        value = raw.headers.get("x-ratelimit-requests-remaining")
        return int(value) if value is not None and value.isdigit() else None

    # --- fixtures by day ---

    def fetch_day(self, day: date) -> RawResponse:
        return self._http.get_json(
            "/fixtures", params={"date": day.isoformat(), "timezone": REQUEST_TZ}
        )

    def normalize_day(self, raw: RawResponse) -> list[FixturesBatch]:
        """One FixturesBatch per league (the shape upsert_batch already handles)."""
        items = self._items(raw, _Item)
        by_league: dict[tuple[int, int], list[_Item]] = defaultdict(list)
        for it in items:
            by_league[(it.league.id, it.league.season)].append(it)
        batches = []
        for (_league_id, season), group in by_league.items():
            lg = group[0].league
            try:
                batches.append(
                    FixturesBatch(
                        source=self.source,
                        competition=CompetitionDTO(
                            external_id=str(lg.id),
                            name=lg.name,
                            sport=Sport.FOOTBALL,
                            country_code=country_code_from_flag(lg.flag),
                            country_name=lg.country,
                        ),
                        season=SeasonDTO(external_id=f"{lg.id}-{season}", name=str(season)),
                        fixtures=[self._fixture(it) for it in group],
                    )
                )
            except (ValidationError, ValueError) as exc:
                raise FormatError(f"unexpected fixture data in league {lg.id}: {exc}") from exc
        return batches

    @staticmethod
    def _fixture(it: _Item) -> FixtureDTO:
        status, confirmed = STATUS_MAP.get(it.fixture.status.short, (EventStatus.UNKNOWN, False))
        if status is EventStatus.UNKNOWN:
            log.warning("api_football_unknown_status", status=it.fixture.status.short,
                        fixture_id=it.fixture.id)  # fmt: skip

        def score(half: _Half) -> ScoreDTO | None:
            if half.home is None or half.away is None:
                return None
            return ScoreDTO(home=half.home, away=half.away)

        played = it.fixture.status.short in PLAYED_STATUSES
        full_time = score(it.score.fulltime) if played else None
        return FixtureDTO(
            external_id=str(it.fixture.id),
            starts_at=it.fixture.date,
            kickoff_confirmed=confirmed,
            status=status,
            round=it.league.round,
            venue=it.fixture.venue.name if it.fixture.venue else None,
            home=TeamDTO(external_id=str(it.teams.home.id), name=it.teams.home.name),
            away=TeamDTO(external_id=str(it.teams.away.id), name=it.teams.away.name),
            full_time=full_time,
            half_time=score(it.score.halftime) if full_time else None,
        )

    # --- odds ---

    def fetch_odds_page(self, day: date, page: int) -> RawResponse:
        return self._http.get_json(
            "/odds", params={"date": day.isoformat(), "timezone": REQUEST_TZ, "page": page}
        )

    def fetch_fixture_odds(self, fixture_external_id: str) -> RawResponse:
        return self._http.get_json("/odds", params={"fixture": fixture_external_id})

    def normalize_odds(self, raw: RawResponse) -> list[FixtureOdds]:
        out = []
        for it in self._items(raw, _OddsItem):
            prices: list[PriceDTO] = []
            for book in it.bookmakers:
                winner = next((b for b in book.bets if b.name == "Match Winner"), None)
                if winner is None:
                    continue
                try:
                    values = [_OddValue.model_validate(v) for v in winner.values]
                except ValidationError:
                    values = []
                if {v.value for v in values} != set(OUTCOMES):
                    continue  # not a clean 1X2 from this bookmaker: skip it, never guess
                try:
                    quotes = [
                        PriceDTO(
                            bookmaker=book.name,
                            market=Market.H2H,
                            selection=OUTCOMES[v.value],
                            price=Decimal(v.odd),  # sent as a decimal string: exact
                            captured_at=it.update,
                        )
                        for v in values
                    ]
                except (ValidationError, InvalidOperation):
                    log.warning("api_football_invalid_price", fixture_id=it.fixture.id,
                                bookmaker=book.name)  # fmt: skip
                    continue
                prices.extend(quotes)
            out.append(FixtureOdds(fixture_external_id=str(it.fixture.id), prices=prices))
        return out

    @staticmethod
    def _items(raw: RawResponse, model: type[BaseModel]) -> list[Any]:
        payload = raw.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), list):
            raise FormatError("API-Football: expected an object with a 'response' list")
        try:
            return [model.model_validate(x) for x in payload["response"]]
        except ValidationError as exc:
            raise FormatError(
                f"unexpected API-Football payload: {exc.error_count()} errors"
            ) from exc

    # --- FixturesProvider protocol (season-based: refused on the free plan) ---

    def fetch_fixtures(
        self,
        competition: str,
        *,
        season: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> RawResponse:
        if competition not in LEAGUE_IDS:
            raise ProviderError(f"unknown competition code for API-Football: {competition}")
        params: dict[str, str | int] = {"league": LEAGUE_IDS[competition]}
        if season is not None:
            params["season"] = season
        if date_from is not None:
            params["from"] = date_from.isoformat()
        if date_to is not None:
            params["to"] = date_to.isoformat()
        return self._http.get_json("/fixtures", params=params)

    def normalize(self, raw: RawResponse) -> FixturesBatch:
        batches = self.normalize_day(raw)
        if len(batches) != 1:
            raise FormatError(f"expected one league in the response, got {len(batches)}")
        return batches[0]
