"""Common contract for news providers."""

from typing import Protocol

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
    articles: list[ArticleDTO]


class NewsProvider(Protocol):
    source: Provider
    page_size: int

    def fetch_news(self) -> RawResponse: ...

    def normalize(self, raw: RawResponse) -> NewsBatch: ...

    def close(self) -> None: ...
