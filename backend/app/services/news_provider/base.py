"""Common contract for news providers."""

from typing import Any, Protocol

from pydantic import AwareDatetime, BaseModel

from app.models.enums import Provider
from app.services.http import RawResponse


class ArticleDTO(BaseModel):
    url: str
    title: str
    description: str | None = None
    # Provider's text snippet: used ONLY to classify the article, never stored
    # (copyright: we keep a short summary and link to the source).
    content: str | None = None
    publisher: str | None = None
    published_at: AwareDatetime


class NewsBatch(BaseModel):
    source: Provider
    language: str
    total_results: int | None = None
    page_count: int = 0  # articles in the provider page, before any filtering
    oldest_in_page: AwareDatetime | None = None
    articles: list[ArticleDTO]


class NewsProvider(Protocol):
    source: Provider
    page_size: int

    def fetch_news(self) -> RawResponse: ...

    def normalize(self, raw: RawResponse) -> NewsBatch: ...

    def redact_raw(self, payload: Any) -> Any:
        """The part of the raw response we may keep for audit."""
        ...

    def close(self) -> None: ...
