from datetime import datetime

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow
from app.db.types import JSONType, UTCDateTime
from app.models.enums import IngestionStatus, Provider, QuotaPeriod, db_enum


class IngestionRun(Base):
    """One call to an external provider: raw response kept for audit and reprocessing.

    `request_url` must never contain the API key (clients strip it before saving).
    """

    __tablename__ = "ingestion_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Provider] = mapped_column(db_enum(Provider, "provider"))
    job: Mapped[str] = mapped_column(String(40))  # fixtures | results | odds | news
    status: Mapped[IngestionStatus] = mapped_column(
        db_enum(IngestionStatus, "ingestion_status"), default=IngestionStatus.RUNNING
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    request_url: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None]
    attempts: Mapped[int] = mapped_column(default=0)
    raw_payload: Mapped[dict | list | None] = mapped_column(JSONType)
    records_written: Mapped[int] = mapped_column(default=0)
    quota_remaining: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_ingestion_run_source_job", "source", "job", "started_at"),)


class ProviderQuota(Base):
    """Quota counters per provider, kept in sync with the providers' response headers."""

    __tablename__ = "provider_quota"

    source: Mapped[Provider] = mapped_column(db_enum(Provider, "provider"), primary_key=True)
    period: Mapped[QuotaPeriod] = mapped_column(db_enum(QuotaPeriod, "quota_period"))
    limit: Mapped[int]
    used: Mapped[int] = mapped_column(default=0)
    remaining: Mapped[int | None]  # last value reported by the provider, if any
    period_start: Mapped[datetime] = mapped_column(UTCDateTime())
    exhausted_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
