"""football-data.org v4 adapter (plan B, active: API-Football free plan lacks 2026-27).

Free tier: 12 competitions incl. LaLiga ("PD"), 10 requests/minute, delayed scores.
Auth: `X-Auth-Token` header. Rate-limit headers: X-Requests-Available-Minute,
X-RequestCounter-Reset (seconds until the per-minute counter resets).
"""

from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.models.enums import EventStatus, Provider, Sport
from app.services.errors import FormatError, ProviderError, ThrottledError
from app.services.fixtures_provider.base import (
    CompetitionDTO,
    FixtureDTO,
    FixturesBatch,
    ScoreDTO,
    SeasonDTO,
    TeamDTO,
    season_name,
)
from app.services.http import ProviderHttpClient, RawResponse

log = get_logger(__name__)

BASE_URL = "https://api.football-data.org/v4"

STATUS_MAP: dict[str, EventStatus] = {
    "SCHEDULED": EventStatus.SCHEDULED,
    "TIMED": EventStatus.SCHEDULED,
    "IN_PLAY": EventStatus.LIVE,
    "PAUSED": EventStatus.LIVE,
    "LIVE": EventStatus.LIVE,
    "FINISHED": EventStatus.FINISHED,
    # AWARDED (administrative result: walkover, ineligible player...) is "finished" for
    # the calendar, but its score is NOT a played result: see _fixture().
    "AWARDED": EventStatus.FINISHED,
    "POSTPONED": EventStatus.POSTPONED,
    "SUSPENDED": EventStatus.POSTPONED,
    "CANCELLED": EventStatus.CANCELLED,
}

# Statuses whose utcDate is a real, still-valid kick-off. Everything else is NOT confirmed:
# SCHEDULED (00:00Z date-only placeholder), POSTPONED/SUSPENDED (original slot that won't
# happen), CANCELLED, AWARDED (no match played) and any status we don't know.
KICKOFF_CONFIRMED_STATUSES = {"TIMED", "IN_PLAY", "PAUSED", "LIVE", "FINISHED"}

# football-data uses its own 3-letter area codes; we store ISO alpha-2.
AREA_TO_ISO2 = {"ESP": "ES", "ITA": "IT", "DEU": "DE", "FRA": "FR", "PRT": "PT", "NLD": "NL"}


# --- Minimal schema of the fields we rely on (extra fields are ignored) ---
class _Team(BaseModel):
    id: int
    name: str
    shortName: str | None = None


class _Half(BaseModel):
    home: int | None = None
    away: int | None = None


class _Score(BaseModel):
    duration: str = "REGULAR"  # REGULAR | EXTRA_TIME | PENALTY_SHOOTOUT
    fullTime: _Half
    halfTime: _Half | None = None
    # Present when duration != REGULAR: the 90-minute score (what 1X2 settles on).
    regularTime: _Half | None = None


class _Season(BaseModel):
    id: int
    startDate: date | None = None
    endDate: date | None = None


class _Area(BaseModel):
    code: str | None = None


class _Match(BaseModel):
    id: int
    utcDate: str
    status: str
    matchday: int | None = None
    venue: str | None = None
    area: _Area | None = None  # v4 puts the area on each match
    season: _Season
    homeTeam: _Team
    awayTeam: _Team
    score: _Score


class _Competition(BaseModel):
    id: int
    name: str
    code: str | None = None


class _MatchesResponse(BaseModel):
    competition: _Competition
    matches: list[_Match]
    area: _Area | None = None  # top-level area isn't always present


def _classify_body(response: httpx.Response, payload: Any) -> ProviderError | None:
    if response.status_code == 429:
        # Per-minute limit: transient. Let the HTTP layer back off (honors the reset header).
        return None
    if isinstance(payload, dict) and payload.get("errorCode") and response.status_code < 400:
        return ProviderError(f"provider error in body: {payload.get('message')}")
    return None


class FootballDataProvider:
    source = Provider.FOOTBALL_DATA

    def __init__(self, api_key: str, *, client: ProviderHttpClient | None = None) -> None:
        self._http = client or ProviderHttpClient(
            source=self.source.value,
            base_url=BASE_URL,
            auth_headers={"X-Auth-Token": api_key},
            classify_body=_classify_body,
        )

    def close(self) -> None:
        self._http.close()

    def fetch_fixtures(
        self,
        competition: str,
        *,
        season: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> RawResponse:
        params: dict[str, str | int] = {}
        if season is not None:
            params["season"] = season
        if date_from is not None:
            params["dateFrom"] = date_from.isoformat()
        if date_to is not None:
            params["dateTo"] = date_to.isoformat()
        try:
            return self._http.get_json(f"/competitions/{competition}/matches", params=params)
        except ThrottledError:
            log.warning("football_data_throttled", competition=competition)
            raise

    def quota_remaining(self, raw: RawResponse) -> int | None:
        # Only a per-minute counter is exposed; our daily budget is tracked locally.
        return None

    def normalize(self, raw: RawResponse) -> FixturesBatch:
        try:
            parsed = _MatchesResponse.model_validate(raw.payload)
        except ValidationError as exc:
            raise FormatError(
                f"unexpected football-data payload: {exc.error_count()} errors"
            ) from exc

        if not parsed.matches:
            raise FormatError("football-data returned no matches (season not available?)")

        season_ids = {m.season.id for m in parsed.matches}
        if len(season_ids) > 1:
            # A date-range call across seasons: refuse rather than file matches under the
            # wrong season. Callers must request one season at a time.
            raise FormatError(f"matches from several seasons in one response: {season_ids}")
        first_season = parsed.matches[0].season
        area = parsed.area or parsed.matches[0].area
        area_code = area.code if area else None
        try:
            competition = CompetitionDTO(
                external_id=str(parsed.competition.id),
                name=parsed.competition.name,
                sport=Sport.FOOTBALL,
                country_code=AREA_TO_ISO2.get(area_code or ""),
            )
            season = SeasonDTO(
                external_id=str(first_season.id),
                name=season_name(first_season.startDate, first_season.endDate),
                start_date=first_season.startDate,
                end_date=first_season.endDate,
            )
        except ValidationError as exc:
            raise FormatError(f"unexpected competition/season data: {exc}") from exc
        try:
            fixtures = [self._fixture(m) for m in parsed.matches]
            return FixturesBatch(
                source=self.source, competition=competition, season=season, fixtures=fixtures
            )
        except (ValidationError, ValueError) as exc:
            raise FormatError(f"unexpected match data: {exc}") from exc

    @staticmethod
    def _fixture(m: _Match) -> FixtureDTO:
        status = STATUS_MAP.get(m.status)
        if status is None:
            log.warning("football_data_unknown_status", status=m.status, match_id=m.id)
            status = EventStatus.UNKNOWN

        def score(half: _Half | None) -> ScoreDTO | None:
            if half is None or half.home is None or half.away is None:
                return None
            return ScoreDTO(home=half.home, away=half.away)

        # Only a match actually played to the end produces a result. For extra time /
        # penalties, fullTime includes them, so we take the 90-minute regularTime (and
        # record nothing if the provider doesn't give it).
        full_time = half_time = None
        if m.status == "FINISHED":
            if m.score.duration == "REGULAR":
                full_time = score(m.score.fullTime)
            else:
                full_time = score(m.score.regularTime)
                if full_time is None:
                    log.warning("football_data_no_regular_time", match_id=m.id,
                                duration=m.score.duration)  # fmt: skip
            half_time = score(m.score.halfTime) if full_time else None
        elif m.status == "AWARDED":
            log.warning("football_data_awarded_result_skipped", match_id=m.id)

        return FixtureDTO(
            external_id=str(m.id),
            starts_at=m.utcDate,  # ISO 8601 with "Z": pydantic parses to aware UTC
            # Decided by status, not by the 00:00 time: a real match could start at midnight.
            kickoff_confirmed=m.status in KICKOFF_CONFIRMED_STATUSES,
            status=status,
            round=f"Jornada {m.matchday}" if m.matchday else None,
            venue=m.venue,
            home=TeamDTO(external_id=str(m.homeTeam.id), name=m.homeTeam.name,
                         short_name=m.homeTeam.shortName),
            away=TeamDTO(external_id=str(m.awayTeam.id), name=m.awayTeam.name,
                         short_name=m.awayTeam.shortName),
            full_time=full_time,
            half_time=half_time,
        )  # fmt: skip
