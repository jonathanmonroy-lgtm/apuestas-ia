from pydantic import SecretStr

from app.core.config import Settings
from app.services.fixtures_provider import ProviderNotConfiguredError
from app.services.odds_provider.base import OddsBatch, OddsProvider


def _secret(value: SecretStr | None) -> str:
    return value.get_secret_value().strip() if value is not None else ""


def get_odds_provider(settings: Settings) -> OddsProvider:
    from app.services.odds_provider.the_odds_api import TheOddsApiProvider

    if not _secret(settings.odds_api_key):
        raise ProviderNotConfiguredError("ODDS_API_KEY is not set")
    return TheOddsApiProvider(_secret(settings.odds_api_key), regions=settings.odds_regions)


__all__ = ["OddsBatch", "OddsProvider", "get_odds_provider"]
