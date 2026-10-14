"""HTTP layer shared by all provider clients: retries, backoff and error classification.

Policy (see docs/arquitectura.md):
- 429 / 5xx / network errors are transient: exponential backoff honoring Retry-After,
  at most `max_attempts` attempts in total.
- 401/403 -> AuthError, never retried.
- Providers can inspect 200 bodies via `classify_body` (API-Sports puts errors there).
- The API key travels in a header when the provider allows it. Providers that only accept
  it as a query param (The Odds API) pass `auth_params`: they are sent but stripped from
  the URL we store/log.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.services.errors import (
    AuthError,
    FormatError,
    ProviderError,
    ProviderUnavailableError,
    ThrottledError,
)

log = get_logger(__name__)

MAX_BACKOFF_SECONDS = 60.0


@dataclass(frozen=True)
class RawResponse:
    """What a client returns: the untouched JSON plus metadata for IngestionRun."""

    payload: Any
    url: str  # sanitized: no credentials
    http_status: int
    headers: Mapping[str, str]
    attempts: int
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


BodyClassifier = Callable[[httpx.Response, Any], ProviderError | None]


def retry_after_seconds(response: httpx.Response, attempt: int, base: float) -> float:
    # football-data.org sends X-RequestCounter-Reset (seconds) instead of Retry-After.
    header = response.headers.get("Retry-After") or response.headers.get("X-RequestCounter-Reset")
    if header is not None:
        try:
            return min(max(float(header), 0.0), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass  # HTTP-date form: fall back to exponential backoff
    return min(base * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)


class ProviderHttpClient:
    def __init__(
        self,
        *,
        source: str,
        base_url: str,
        auth_headers: Mapping[str, str] | None = None,
        auth_params: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        max_attempts: int = 3,
        backoff_base: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        classify_body: BodyClassifier | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.source = source
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self._sleep = sleep
        self._classify_body = classify_body
        self._auth_params = dict(auth_params or {})
        self._client = httpx.Client(
            base_url=base_url,
            headers=dict(auth_headers or {}),
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def get_json(self, path: str, params: Mapping[str, str | int] | None = None) -> RawResponse:
        last_error: ProviderError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.get(path, params={**(params or {}), **self._auth_params})
            except httpx.TransportError as exc:
                last_error = ProviderUnavailableError(
                    f"network error: {exc.__class__.__name__}", attempts=attempt
                )
                self._backoff(attempt, None, reason="network")
                continue

            url = self._sanitized_url(response.request.url)
            if response.status_code == 429:
                last_error = ThrottledError("rate limited (429)", http_status=429, attempts=attempt)
                # A provider may say a 429 is really "quota exhausted": don't retry that.
                classified = self._classify(response)
                if classified is not None:
                    classified.attempts = attempt
                    raise classified
                self._backoff(attempt, response, reason="429")
                continue
            if response.status_code in (401, 403):
                raise self._classify(response) or AuthError(
                    f"HTTP {response.status_code}: {_short_body(response)}",
                    http_status=response.status_code,
                    attempts=attempt,
                )
            if response.status_code >= 500:
                last_error = ProviderUnavailableError(
                    f"HTTP {response.status_code}", http_status=response.status_code,
                    attempts=attempt,
                )  # fmt: skip
                self._backoff(attempt, response, reason="5xx")
                continue
            if response.status_code >= 400:
                raise self._classify(response) or ProviderError(
                    f"HTTP {response.status_code}: {_short_body(response)}",
                    http_status=response.status_code,
                    attempts=attempt,
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise FormatError(
                    "response is not valid JSON", http_status=response.status_code,
                    attempts=attempt,
                ) from exc  # fmt: skip
            error = self._classify(response, payload)
            if error is not None:
                error.attempts = attempt
                raise error
            return RawResponse(
                payload=payload,
                url=url,
                http_status=response.status_code,
                headers=dict(response.headers),
                attempts=attempt,
            )

        # Only reachable after retrying, so last_error is set (max_attempts >= 1).
        raise last_error or ProviderUnavailableError("no attempts made")

    def _sanitized_url(self, url: httpx.URL) -> str:
        for key in self._auth_params:
            url = url.copy_remove_param(key)
        return str(url)

    def _classify(self, response: httpx.Response, payload: Any = None) -> ProviderError | None:
        if self._classify_body is None:
            return None
        if payload is None:
            try:
                payload = response.json()
            except ValueError:
                payload = None
        return self._classify_body(response, payload)

    def _backoff(self, attempt: int, response: httpx.Response | None, *, reason: str) -> None:
        if attempt >= self.max_attempts:
            return
        delay = (
            retry_after_seconds(response, attempt, self.backoff_base)
            if response is not None
            else min(self.backoff_base * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
        )
        log.warning(
            "provider_retry", source=self.source, attempt=attempt, reason=reason, delay_s=delay
        )
        self._sleep(delay)


def _short_body(response: httpx.Response, limit: int = 300) -> str:
    return response.text[:limit]
