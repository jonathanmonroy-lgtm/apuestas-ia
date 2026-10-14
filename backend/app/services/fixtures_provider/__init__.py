from pydantic import SecretStr

from app.core.config import Settings
from app.services.fixtures_provider.base import FixturesBatch, FixturesProvider


def _secret(value: SecretStr | None) -> str:
    """`KEY=` in .env yields an empty SecretStr, not None: treat both as unset."""
    return value.get_secret_value().strip() if value is not None else ""


class ProviderNotConfiguredError(RuntimeError):
    pass


def get_fixtures_provider(settings: Settings) -> FixturesProvider:
    """Pick the implementation from FIXTURES_PROVIDER. No plugin system on purpose."""
    if settings.fixtures_provider == "football_data":
        from app.services.fixtures_provider.football_data import FootballDataProvider

        if not _secret(settings.football_data_key):
            raise ProviderNotConfiguredError("FOOTBALL_DATA_KEY is not set")
        return FootballDataProvider(_secret(settings.football_data_key))

    if settings.fixtures_provider == "api_football":
        from app.services.fixtures_provider.api_football import ApiFootballProvider

        if not _secret(settings.api_football_key):
            raise ProviderNotConfiguredError("API_FOOTBALL_KEY is not set")
        return ApiFootballProvider(_secret(settings.api_football_key))

    raise ProviderNotConfiguredError(f"unknown fixtures provider {settings.fixtures_provider}")


__all__ = [
    "FixturesBatch",
    "FixturesProvider",
    "ProviderNotConfiguredError",
    "get_fixtures_provider",
]
