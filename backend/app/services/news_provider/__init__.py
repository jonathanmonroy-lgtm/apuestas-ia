from pydantic import SecretStr

from app.core.config import Settings
from app.services.fixtures_provider import ProviderNotConfiguredError
from app.services.news_provider.base import NewsBatch, NewsProvider


def _secret(value: SecretStr | None) -> str:
    return value.get_secret_value().strip() if value is not None else ""


def get_news_provider(settings: Settings) -> NewsProvider:
    from app.services.news_provider.newsapi import NewsApiProvider

    if not _secret(settings.newsapi_key):
        raise ProviderNotConfiguredError("NEWSAPI_KEY is not set")
    return NewsApiProvider(_secret(settings.newsapi_key))


__all__ = ["NewsBatch", "NewsProvider", "get_news_provider"]
