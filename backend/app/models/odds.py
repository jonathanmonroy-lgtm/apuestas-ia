from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow
from app.db.types import JSONType, Price, UTCDateTime
from app.models.enums import Market, Provider, Selection, db_enum


class OddSnapshot(Base):
    """A price observed at a point in time. APPEND-ONLY.

    Rows are never updated or deleted, with one exception: `is_closing` is flipped
    once by the closing-line job (last snapshot before kick-off). Price fields are
    immutable. History is the raw material for the model and for closing-line value.
    """

    __tablename__ = "odd_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"))
    source: Mapped[Provider] = mapped_column(db_enum(Provider, "provider"))
    # Stored, but hidden from public endpoints while INTERNAL MODE is on.
    bookmaker: Mapped[str] = mapped_column(String(60))
    market: Mapped[Market] = mapped_column(db_enum(Market, "market"))
    selection: Mapped[Selection] = mapped_column(db_enum(Selection, "selection"))
    line: Mapped[Decimal | None] = mapped_column(Price())  # handicap/total; NULL for 1X2
    price: Mapped[Decimal] = mapped_column(Price())  # decimal odds
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime())  # provider's last_update
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_run.id"))
    is_closing: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        CheckConstraint("CAST(price AS REAL) > 1", name="price_gt_one"),
        Index("ix_odd_snapshot_lookup", "event_id", "market", "selection", "captured_at"),
    )


class UnmatchedOdd(Base):
    """Odds whose event could not be matched to an Event. Never creates an Event."""

    __tablename__ = "unmatched_odd"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Provider] = mapped_column(db_enum(Provider, "provider"))
    external_event_id: Mapped[str] = mapped_column(String(120))
    home_name: Mapped[str] = mapped_column(String(200))
    away_name: Mapped[str] = mapped_column(String(200))
    commence_time: Mapped[datetime] = mapped_column(UTCDateTime())
    reason: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONType)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_run.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (Index("ix_unmatched_odd_open", "source", "resolved_at"),)
