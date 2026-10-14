from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.enums import EventStatus, Provider, db_enum
from app.models.reference import Competition, Participant, Season


class Event(TimestampMixin, Base):
    """A match. The fixtures provider is the source of truth for this table."""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id"))
    season_id: Mapped[int | None] = mapped_column(ForeignKey("season.id"))
    home_participant_id: Mapped[int] = mapped_column(ForeignKey("participant.id"))
    away_participant_id: Mapped[int] = mapped_column(ForeignKey("participant.id"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[EventStatus] = mapped_column(
        db_enum(EventStatus, "event_status"), default=EventStatus.SCHEDULED
    )
    round: Mapped[str | None] = mapped_column(String(60))
    venue: Mapped[str | None] = mapped_column(String(120))

    competition: Mapped[Competition] = relationship()
    season: Mapped[Season | None] = relationship()
    home: Mapped[Participant] = relationship(foreign_keys=[home_participant_id])
    away: Mapped[Participant] = relationship(foreign_keys=[away_participant_id])
    result: Mapped["EventResult | None"] = relationship(back_populates="event")

    __table_args__ = (
        CheckConstraint("home_participant_id <> away_participant_id", name="distinct_sides"),
        Index("ix_event_starts_at", "starts_at"),
        Index("ix_event_competition_starts_at", "competition_id", "starts_at"),
    )


class EventResult(TimestampMixin, Base):
    """Final score. Needed to settle bets and as training label."""

    __tablename__ = "event_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"), unique=True)
    home_score: Mapped[int]
    away_score: Mapped[int]
    home_score_ht: Mapped[int | None]
    away_score_ht: Mapped[int | None]
    source: Mapped[Provider] = mapped_column(db_enum(Provider, "provider"))
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    event: Mapped[Event] = relationship(back_populates="result")

    __table_args__ = (
        CheckConstraint("home_score >= 0 AND away_score >= 0", name="non_negative_scores"),
    )
