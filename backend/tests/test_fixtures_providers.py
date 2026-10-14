import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.models.enums import EventStatus, Provider
from app.services.errors import AuthError, FormatError, QuotaExhaustedError, ThrottledError
from app.services.fixtures_provider import ProviderNotConfiguredError, get_fixtures_provider
from app.services.fixtures_provider.api_football import classify_body
from app.services.fixtures_provider.football_data import BASE_URL as FD_BASE_URL
from app.services.fixtures_provider.football_data import FootballDataProvider
from app.services.fixtures_provider.football_data import _classify_body as fd_classify_body
from app.services.http import ProviderHttpClient, RawResponse

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def raw(payload) -> RawResponse:
    return RawResponse(payload=payload, url="https://x", http_status=200, headers={}, attempts=1)


# --- football-data.org (active provider) ---
# Real response trimmed to 5 matches (GET /v4/competitions/PD/matches, recorded 2026-10-14).
REAL = "football_data/matches_pd_2026_real.json"


def test_football_data_normalize_real_payload():
    batch = FootballDataProvider("k").normalize(raw(load(REAL)))

    assert batch.source is Provider.FOOTBALL_DATA
    assert batch.competition.external_id == "2014"
    assert batch.competition.name == "Primera Division"
    assert batch.competition.country_code == "ES"  # from the per-match area
    assert batch.season.external_id == "2518"
    assert batch.season.name == "2026-27"
    assert batch.season.start_date == date(2026, 8, 16)

    by_id = {f.external_id: f for f in batch.fixtures}
    finished = by_id["564634"]
    assert finished.status is EventStatus.FINISHED
    assert finished.starts_at == datetime(2026, 8, 15, 17, 30, tzinfo=UTC)
    assert finished.home.name == "Deportivo Alavés"
    assert finished.home.short_name == "Alavés"
    assert (finished.full_time.home, finished.full_time.away) == (3, 0)
    assert (finished.half_time.home, finished.half_time.away) == (0, 0)
    assert finished.round == "Jornada 1"
    assert finished.venue is None  # v4 match list has no venue

    assert by_id["564716"].status is EventStatus.SCHEDULED  # TIMED
    assert by_id["564716"].full_time is None
    assert by_id["564748"].status is EventStatus.SCHEDULED


def test_football_data_cancelled_match_gets_no_result():
    """Real quirk: the cancelled Rayo - Athletic comes with fullTime 0-0 and winner DRAW."""
    batch = FootballDataProvider("k").normalize(raw(load(REAL)))
    cancelled = next(f for f in batch.fixtures if f.external_id == "564698")
    assert cancelled.status is EventStatus.CANCELLED
    assert cancelled.full_time is None
    assert cancelled.half_time is None


def test_football_data_unknown_status_is_kept_as_unknown():
    payload = load(REAL)
    payload["matches"][2]["status"] = "SOMETHING_NEW"
    batch = FootballDataProvider("k").normalize(raw(payload))
    assert batch.fixtures[2].status is EventStatus.UNKNOWN


@pytest.mark.parametrize(
    "payload",
    [{"matches": []}, {"competition": {"id": 1, "name": "x"}, "matches": []}, ["not", "a", "dict"]],
)
def test_football_data_unexpected_payload_is_format_error(payload):
    with pytest.raises(FormatError):
        FootballDataProvider("k").normalize(raw(payload))


# --- API-Football (inactive; kept for its error classification) ---


def test_api_football_free_plan_season_error_is_auth_error():
    """Real response recorded 2026-10-14: HTTP 200 with the error in the body."""
    payload = load("api_football/fixtures_laliga_2026.json")
    error = classify_body(httpx.Response(200), payload)
    assert isinstance(error, AuthError)
    assert "Free plans" in str(error)


def test_api_football_quota_error_is_quota_exhausted():
    payload = {"errors": {"requests": "You have reached the request limit for the day"}}
    assert isinstance(classify_body(httpx.Response(200), payload), QuotaExhaustedError)


def test_api_football_no_errors():
    assert classify_body(httpx.Response(200), {"errors": [], "response": []}) is None


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_or_blank_key_is_not_configured(key):
    settings = Settings(fixtures_provider="football_data", football_data_key=key)
    with pytest.raises(ProviderNotConfiguredError):
        get_fixtures_provider(settings)


def football_data_with(handler, sleeps: list[float]) -> FootballDataProvider:
    client = ProviderHttpClient(
        source="football_data",
        base_url=FD_BASE_URL,
        auth_headers={"X-Auth-Token": "k"},
        classify_body=fd_classify_body,
        sleep=sleeps.append,
        transport=httpx.MockTransport(handler),
    )
    return FootballDataProvider("k", client=client)


def test_football_data_request_shape():
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=load(REAL))

    football_data_with(handler, []).fetch_fixtures(
        "PD", season=2026, date_from=date(2026, 10, 1), date_to=date(2026, 10, 31)
    )
    req = seen[0]
    assert req.url.path == "/v4/competitions/PD/matches"
    assert dict(req.url.params) == {
        "season": "2026", "dateFrom": "2026-10-01", "dateTo": "2026-10-31",
    }  # fmt: skip
    assert req.headers["X-Auth-Token"] == "k"


def test_football_data_429_waits_for_counter_reset():
    calls = iter([
        httpx.Response(
            429, headers={"X-RequestCounter-Reset": "37"},
            json={"message": "You reached your request limit. Wait 37 seconds.", "errorCode": 429},
        ),
        httpx.Response(200, json=load(REAL)),
    ])  # fmt: skip
    sleeps: list[float] = []
    raw_response = football_data_with(lambda req: next(calls), sleeps).fetch_fixtures("PD")
    assert sleeps == [37.0]
    assert raw_response.attempts == 2


def test_football_data_403_is_auth_error():
    """403 = competition/season outside our plan: needs a human, never retried."""
    sleeps: list[float] = []
    provider = football_data_with(
        lambda req: httpx.Response(403, json={"message": "restricted", "errorCode": 403}), sleeps
    )
    with pytest.raises(AuthError):
        provider.fetch_fixtures("CL")
    assert sleeps == []


def test_football_data_kickoff_confirmed_real_vs_placeholder():
    """Real data: SCHEDULED comes with a 00:00Z placeholder, TIMED with the real time."""
    by_id = {
        f.external_id: f for f in FootballDataProvider("k").normalize(raw(load(REAL))).fixtures
    }

    placeholder = by_id["564748"]  # SCHEDULED, utcDate 2026-11-22T00:00:00Z
    assert placeholder.kickoff_confirmed is False
    assert placeholder.starts_at.date() == date(2026, 11, 22)  # the date is still real

    timed = by_id["564716"]  # TIMED, 2026-10-16T19:00:00Z
    assert timed.kickoff_confirmed is True
    assert timed.starts_at == datetime(2026, 10, 16, 19, 0, tzinfo=UTC)

    assert by_id["564634"].kickoff_confirmed is True  # FINISHED
    assert by_id["564698"].kickoff_confirmed is False  # CANCELLED: that slot won't happen


def test_football_data_midnight_timed_match_is_confirmed():
    """The flag comes from the status, not from the 00:00 time."""
    payload = load(REAL)
    payload["matches"][2]["utcDate"] = "2026-10-17T00:00:00Z"  # TIMED at midnight UTC
    fixture = FootballDataProvider("k").normalize(raw(payload)).fixtures[2]
    assert fixture.kickoff_confirmed is True


@pytest.mark.parametrize(
    "status", ["POSTPONED", "SUSPENDED", "CANCELLED", "AWARDED", "SCHEDULED", "SOMETHING_NEW"]
)
def test_kickoff_not_confirmed_unless_status_guarantees_it(status):
    """QA F2 #1: only TIMED/live/FINISHED keep a valid kick-off time."""
    payload = load(REAL)
    payload["matches"][2]["status"] = status  # the TIMED Deportivo - Levante
    fixture = FootballDataProvider("k").normalize(raw(payload)).fixtures[2]
    assert fixture.kickoff_confirmed is False


def test_awarded_score_is_not_stored_as_played_result():
    """QA F2 #2: a 3-0 by walkover is administrative, not a football result."""
    payload = load(REAL)
    payload["matches"][0]["status"] = "AWARDED"  # Alavés 3-0 Getafe
    fixture = FootballDataProvider("k").normalize(raw(payload)).fixtures[0]
    assert fixture.status is EventStatus.FINISHED
    assert fixture.full_time is None
    assert fixture.half_time is None


def test_penalty_shootout_uses_regular_time_score():
    """QA F2 #3: 1X2 settles on 90 minutes; fullTime would include ET/penalties."""
    payload = load(REAL)
    payload["matches"][0]["score"] = {
        "winner": "HOME_TEAM", "duration": "PENALTY_SHOOTOUT",
        "fullTime": {"home": 5, "away": 4}, "halfTime": {"home": 0, "away": 1},
        "regularTime": {"home": 1, "away": 1}, "extraTime": {"home": 0, "away": 0},
        "penalties": {"home": 4, "away": 3},
    }  # fmt: skip
    fixture = FootballDataProvider("k").normalize(raw(payload)).fixtures[0]
    assert (fixture.full_time.home, fixture.full_time.away) == (1, 1)


def test_extra_time_without_regular_time_records_nothing():
    payload = load(REAL)
    payload["matches"][0]["score"]["duration"] = "EXTRA_TIME"  # and no regularTime
    fixture = FootballDataProvider("k").normalize(raw(payload)).fixtures[0]
    assert fixture.full_time is None


def test_matches_from_two_seasons_are_refused():
    """QA F2 #7: never file matches under the wrong season."""
    payload = load(REAL)
    payload["matches"][0]["season"] = {"id": 2400, "startDate": "2025-08-15",
                                       "endDate": "2026-05-24"}  # fmt: skip
    with pytest.raises(FormatError, match="several seasons"):
        FootballDataProvider("k").normalize(raw(payload))


def test_api_football_per_minute_rate_limit_is_throttled_not_exhausted():
    """QA F2 #9: rateLimit is per-minute; pausing the provider for a day would be wrong."""
    payload = {
        "errors": {"rateLimit": "Too many requests. Your rate limit is 10 requests per minute."}
    }
    assert isinstance(classify_body(httpx.Response(200), payload), ThrottledError)
