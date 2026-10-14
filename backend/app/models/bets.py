from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.types import Money, Price, UTCDateTime
from app.models.enums import BetStatus, Market, Selection, db_enum
from app.models.events import Event


class Bet(TimestampMixin, Base):
    """A bet the user placed elsewhere and logs here. Singles only in the MVP."""

    __tablename__ = "bet"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    # Optional: the user may log a bet on an event we don't ingest (free text label).
    event_id: Mapped[int | None] = mapped_column(ForeignKey("event.id"))
    event_label: Mapped[str] = mapped_column(String(200))
    bookmaker: Mapped[str | None] = mapped_column(String(60))
    market: Mapped[Market] = mapped_column(db_enum(Market, "market"))
    selection: Mapped[Selection] = mapped_column(db_enum(Selection, "selection"))
    line: Mapped[Decimal | None] = mapped_column(Price())
    odds_taken: Mapped[Decimal] = mapped_column(Price())
    stake: Mapped[Decimal] = mapped_column(Money())
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[BetStatus] = mapped_column(
        db_enum(BetStatus, "bet_status"), default=BetStatus.PENDING
    )
    payout: Mapped[Decimal | None] = mapped_column(Money())  # total returned, incl. stake
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    notes: Mapped[str | None] = mapped_column(Text)

    event: Mapped[Event | None] = relationship()

    __table_args__ = (
        CheckConstraint("CAST(stake AS REAL) > 0", name="positive_stake"),
        CheckConstraint("CAST(odds_taken AS REAL) > 1", name="odds_gt_one"),
        CheckConstraint(
            "(status = 'pending' AND settled_at IS NULL) OR "
            "(status <> 'pending' AND settled_at IS NOT NULL)",
            name="settled_consistency",
        ),
        Index("ix_bet_user_placed_at", "user_id", "placed_at"),
    )
