from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    api_football_daily_budget: int = 90  # provider limit: 100/day (on-demand odds use this)
    # On-demand "real odds" requests get their OWN daily cap, counted apart, so users
    # clicking can never starve the scheduled sync (4 passes x 9 calls = 36/day):
    # 36 + 40 <= 90. The scheduled sync is only bound by api_football_daily_budget.
    api_football_on_demand_daily_cap: int = 40
    # The same match is not asked again within this window (an empty answer included).
    on_demand_odds_cooldown_minutes: int = 60
    # Stay under the free plan's 10 requests/minute across worker + on-demand.
    api_football_per_minute_cap: int = 8
    day_sync_interval_hours: float = Field(default=6.0, gt=0)
    # Leagues whose calendar comes from another source (LaLiga = football-data): skipped
    # in the multi-country sync so a match never exists twice.
    api_football_skip_leagues: list[str] = ["140"]
    # Which source fills "matches of the day by country". ONE at a time: both list the same
    # matches under different ids, so the other one's events are hidden from those views.
    # espn: any date, no key, one bookmaker's odds on some matches (unofficial endpoint).
    # api_football: yesterday..tomorrow only, odds on demand from ~9 bookmakers.
    day_source: Literal["espn", "api_football"] = "espn"
    # ESPN publishes no limits: a self-imposed cap. One job every 30 min: a near pass
    # (5 calls) or, every 3 h, a wide one (13 calls) -> 40x5 + 8x13 = ~304/day.
    espn_daily_budget: int = 450
    espn_near_interval_minutes: float = Field(default=30.0, gt=0)
    # Below odds_stale_after_hours (4 h), so far-off days' odds don't look stale between
    # passes while unchanged.
    espn_wide_interval_hours: float = Field(default=3.0, gt=0)
    # The browsable window of days (Colombia's), relative to today.
    espn_days_back: int = Field(default=3, ge=1, le=14)
    espn_days_ahead: int = Field(default=7, ge=1, le=14)
    # ESPN gives no quote time: an unchanged price is stored again only this often.
    espn_odds_refresh_hours: float = Field(default=2.0, gt=0)
    # LaLiga's calendar comes from football-data (by ESPN league slug).
    espn_skip_leagues: list[str] = ["esp.1"]
    # The product's "day" is Colombia's (UTC-5, no daylight saving since 1993).
    local_utc_offset_hours: int = -5

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

    @model_validator(mode="after")
    def _day_source_compatible(self) -> "Settings":
        # Views hide the inactive multi-country source's events by provider: with LaLiga's
        # calendar also from API-Football, DAY_SOURCE=espn would hide LaLiga too.
        if self.day_source == "espn" and self.fixtures_provider == "api_football":
            raise ValueError(
                "DAY_SOURCE=espn is incompatible with FIXTURES_PROVIDER=api_football "
                "(LaLiga would be hidden); use DAY_SOURCE=api_football"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
