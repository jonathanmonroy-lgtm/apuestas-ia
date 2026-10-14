"""NewsAPI.org /v2/everything adapter.

Developer (free) plan, verified 2026-10-14 with real calls: articles arrive with ~24 h
delay, at most 100 results per query (page 2 -> `maximumResultsReached`), 100 requests
per day, and the plan is for development only (not production use). One query ORs the
20 LaLiga teams (team_terms.py), restricted to Spanish and to title+description.
Errors come as HTTP 4xx + {"status": "error", "code", "message"}.
"""

from typing import Any

import httpx
from pydantic import AwareDatetime, BaseModel, ValidationError

from app.core.logging import get_logger
from app.models.enums import Provider
from app.services.errors import AuthError, FormatError, ProviderError, QuotaExhaustedError
from app.services.http import ProviderHttpClient, RawResponse
from app.services.news_provider.base import ArticleDTO, NewsBatch
from app.services.news_provider.team_terms import newsapi_query

log = get_logger(__name__)

BASE_URL = "https://newsapi.org/v2"
PAGE_SIZE = 100  # the free plan's maximum results per query
LANGUAGE = "es"
REMOVED = "[Removed]"  # NewsAPI's placeholder for articles taken down by the publisher

AUTH_CODES = {"apiKeyInvalid", "apiKeyDisabled", "apiKeyMissing"}
QUOTA_CODES = {"rateLimited", "apiKeyExhausted"}


def classify_body(response: httpx.Response, payload: Any) -> ProviderError | None:
    if not isinstance(payload, dict) or payload.get("status") != "error":
        return None
    code = str(payload.get("code"))
    message = f"{code}: {payload.get('message')}"
    if code in AUTH_CODES:
        return AuthError(message, http_status=response.status_code)
    if code in QUOTA_CODES:  # free plan: 100 requests / 24 h
        return QuotaExhaustedError(message, http_status=response.status_code)
    return ProviderError(message, http_status=response.status_code)


def redact_raw(payload: Any) -> Any:
    """Copy of the response safe to keep in ingestion_run.raw_payload: the articles'
    `content` snippet and image URL are third-party material we don't store."""
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        return payload
    articles = [
        {k: v for k, v in a.items() if k not in {"content", "urlToImage"}}
        if isinstance(a, dict)
        else a
        for a in payload["articles"]
    ]
    return {**payload, "articles": articles}


class _Source(BaseModel):
    name: str | None = None


class _Article(BaseModel):
    source: _Source | None = None
    title: str | None = None
    description: str | None = None
    content: str | None = None
    url: str | None = None
    publishedAt: AwareDatetime


class _Response(BaseModel):
    status: str
    totalResults: int | None = None
    articles: list[_Article]


class NewsApiProvider:
    source = Provider.NEWSAPI
    page_size = PAGE_SIZE

    def __init__(self, api_key: str, *, client: ProviderHttpClient | None = None) -> None:
        self._http = client or ProviderHttpClient(
            source=self.source.value,
            base_url=BASE_URL,
            auth_headers={"X-Api-Key": api_key},
            classify_body=classify_body,
        )

    def close(self) -> None:
        self._http.close()

    def redact_raw(self, payload: Any) -> Any:
        return redact_raw(payload)

    def fetch_news(self) -> RawResponse:
        return self._http.get_json(
            "/everything",
            params={
                "q": newsapi_query(),
                "language": LANGUAGE,
                "searchIn": "title,description",
                "sortBy": "publishedAt",
                "pageSize": PAGE_SIZE,
            },
        )

    def normalize(self, raw: RawResponse) -> NewsBatch:
        try:
            parsed = _Response.model_validate(raw.payload)
        except ValidationError as exc:
            raise FormatError(f"unexpected NewsAPI payload: {exc.error_count()} errors") from exc
        articles = []
        for a in parsed.articles:
            if not a.title or a.title == REMOVED or not a.url:
                continue
            if not a.url.startswith(("http://", "https://")):
                log.warning("newsapi_bad_url_skipped", url=a.url[:100])
                continue
            articles.append(
                ArticleDTO(
                    url=a.url,
                    title=a.title,
                    description=a.description,
                    content=a.content,
                    publisher=a.source.name if a.source else None,
                    published_at=a.publishedAt,
                )
            )
        return NewsBatch(
            source=self.source,
            language=LANGUAGE,
            total_results=parsed.totalResults,
            page_count=len(parsed.articles),
            oldest_in_page=min((a.publishedAt for a in parsed.articles), default=None),
            articles=articles,
        )
