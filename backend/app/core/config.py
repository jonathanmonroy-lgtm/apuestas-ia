from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
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
    # football_data is active: API-Football's free plan has no access to season 2026-27
    # (verified with a real call on 2026-10-14).
    fixtures_provider: Literal["api_football", "football_data"] = "football_data"
    odds_provider: Literal["the_odds_api"] = "the_odds_api"
    news_provider: Literal["newsapi"] = "newsapi"

    api_football_key: SecretStr | None = None
    football_data_key: SecretStr | None = None
    odds_api_key: SecretStr | None = None
    newsapi_key: SecretStr | None = None

    # Scope: start with one league end-to-end. Codes are our internal competition codes.
    fixtures_competitions: list[str] = ["PD"]
    # Self-imposed daily budgets (below the providers' real limits).
    football_data_daily_budget: int = 100  # provider limit is 10/min, no daily cap
    api_football_daily_budget: int = 90  # provider limit: 100/day

    # Fixtures sync runs daily, so a day and a bit is the freshness threshold.
    fixtures_stale_after_hours: float = 30.0
    # Worker schedule (UTC). 06:15 UTC: overnight results are in, before European mornings.
    fixtures_sync_hour_utc: int = Field(default=6, ge=0, le=23)
    fixtures_sync_minute: int = Field(default=15, ge=0, le=59)

    # INTERNAL MODE has no login yet (auth strategy pending): every request acts as this
    # single local user. Replace with real authentication before any external user.
    internal_user_email: str = "interno@localhost"
    default_currency: str = "EUR"
    # Raising a spending limit waits this long (lowering is immediate). Jurisdiction-
    # dependent: 24 h is a conservative default until the target country is decided.
    limit_increase_cooling_off_hours: int = Field(default=24, ge=0)

    # --- Compliance defaults (INTERNAL MODE until jurisdiction is decided) ---
    # When True, bookmaker names are stored but never exposed by public endpoints.
    internal_mode: bool = True
    odds_regions: str = "eu"

    # Data older than this is flagged as stale to the user.
    odds_stale_after_hours: float = 4.0
    # The Odds API free plan: 500 credits/month; 1 credit per call (regions=eu, h2h).
    # Self-imposed 450 leaves room for manual runs. Every 3h = 8/day = ~248/month.
    odds_monthly_budget: int = 450
    # Never let the provider's remaining balance drop below this (manual runs, tests).
    odds_credit_reserve: int = 50
    odds_sync_interval_hours: float = Field(default=3.0, gt=0)

    # NewsAPI free (Developer) plan: 100 requests/day, max 100 results per query and
    # ~24 h delay. Hourly = 24 calls/day. Measured 2026-10-14: the 100 results of one
    # real call spanned 20:54-23:21 UTC (~2.5 h of an evening with Champions matches;
    # a quieter window, 2026-10-12, spanned ~5 h), so a longer interval would lose
    # articles on busy days. Budget 50 leaves room for manual runs.
    newsapi_daily_budget: int = 50
    news_sync_interval_hours: float = Field(default=1.0, gt=0)
    news_provider_delay_hours: int = 24  # shown to the user next to the news
    # Our last news sync older than this at worker start -> sync right away.
    news_stale_after_hours: float = 2.0

    # Raw provider JSON kept on ingestion_run for audit/re-processing, then dropped.
    raw_payload_retention_days: int = Field(default=30, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
