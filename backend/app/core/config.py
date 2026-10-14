from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables (and `.env` in dev).

    API keys are SecretStr so they never show up in logs or reprs.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    # SQLite for local prototyping; Postgres (docker-compose) is the target.
    database_url: str = "sqlite:///./apuestas.db"

    # --- External data providers (keys are created manually by the user) ---
    fixtures_provider: Literal["api_football", "football_data"] = "api_football"
    odds_provider: Literal["the_odds_api"] = "the_odds_api"
    news_provider: Literal["newsapi"] = "newsapi"

    api_football_key: SecretStr | None = None
    odds_api_key: SecretStr | None = None
    newsapi_key: SecretStr | None = None

    # --- Compliance defaults (INTERNAL MODE until jurisdiction is decided) ---
    # When True, bookmaker names are stored but never exposed by public endpoints.
    internal_mode: bool = True
    odds_regions: str = "eu"

    # Data older than this is flagged as stale to the user.
    odds_stale_after_hours: float = 4.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
