"""Model output (hard numbers) vs generated text, in separate tables on purpose.

Forecast/ForecastOutcome = statistical inference with full provenance.
Report = text written by Claude *from* a Forecast; never the source of a number.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow
from app.db.types import JSONType, Probability, UTCDateTime
from app.models.enums import Market, ReportStatus, Selection, db_enum


class Forecast(Base):
    __tablename__ = "forecast"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"))
    market: Mapped[Market] = mapped_column(db_enum(Market, "market"))
    model_version: Mapped[str] = mapped_column(String(60))
    features_hash: Mapped[str] = mapped_column(String(64))
    # Only data available before this instant was used (prevents leakage, enables audit).
    data_cutoff_at: Mapped[datetime] = mapped_column(UTCDateTime())
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    outcomes: Mapped[list["ForecastOutcome"]] = relationship(back_populates="forecast")
    reports: Mapped[list["Report"]] = relationship(back_populates="forecast")


class ForecastOutcome(Base):
    """Probability for one selection, next to the de-vigged market probability."""

    __tablename__ = "forecast_outcome"

    id: Mapped[int] = mapped_column(primary_key=True)
    forecast_id: Mapped[int] = mapped_column(ForeignKey("forecast.id", ondelete="CASCADE"))
    selection: Mapped[Selection] = mapped_column(db_enum(Selection, "selection"))
    model_probability: Mapped[Decimal] = mapped_column(Probability())
    market_implied_probability: Mapped[Decimal | None] = mapped_column(Probability())

    forecast: Mapped[Forecast] = relationship(back_populates="outcomes")

    __table_args__ = (
        UniqueConstraint("forecast_id", "selection"),
        CheckConstraint(
            "CAST(model_probability AS REAL) BETWEEN 0 AND 1", name="probability_range"
        ),
    )


class Report(Base):
    __tablename__ = "report"

    id: Mapped[int] = mapped_column(primary_key=True)
    forecast_id: Mapped[int] = mapped_column(ForeignKey("forecast.id"))
    body_text: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(String(40))
    llm_model: Mapped[str] = mapped_column(String(80))
    status: Mapped[ReportStatus] = mapped_column(
        db_enum(ReportStatus, "report_status"), default=ReportStatus.DRAFT
    )
    # Result of checking every number in the text against the Forecast inputs.
    validation_errors: Mapped[list | None] = mapped_column(JSONType)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    forecast: Mapped[Forecast] = relationship(back_populates="reports")
