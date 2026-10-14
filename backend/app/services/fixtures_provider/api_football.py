"""API-Football (api-sports.io) adapter — client side only, currently INACTIVE.

Verified 2026-10-14 with a real call: the free plan rejects season 2026 with HTTP 200 and
`errors.plan = "Free plans do not have access to this season, try from 2022 to 2024."`.
Kept because (a) a paid plan may be chosen later and (b) it encodes API-Sports' quirk of
reporting errors inside 200 bodies. `normalize()` is deliberately not implemented: it
must be written against a real recorded success payload, not guessed.
"""

from datetime import date
from typing import Any

import httpx

from app.models.enums import Provider
from app.services.errors import AuthError, FormatError, ProviderError, QuotaExhaustedError
from app.services.fixtures_provider.base import FixturesBatch
from app.services.http import ProviderHttpClient, RawResponse

BASE_URL = "https://v3.football.api-sports.io"
LEAGUE_IDS = {"PD": 140}  # our competition code -> API-Football league id


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
    if "requests" in errors or "rateLimit" in errors:
        return QuotaExhaustedError(message, http_status=response.status_code)
    if "token" in errors or "plan" in errors or "access" in errors:
        return AuthError(message, http_status=response.status_code)
    return ProviderError(message, http_status=response.status_code)


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

    def quota_remaining(self, raw: RawResponse) -> int | None:
        value = raw.headers.get("x-ratelimit-requests-remaining")
        return int(value) if value is not None and value.isdigit() else None

    def normalize(self, raw: RawResponse) -> FixturesBatch:
        raise FormatError("API-Football normalizer not implemented (no real success payload yet)")
