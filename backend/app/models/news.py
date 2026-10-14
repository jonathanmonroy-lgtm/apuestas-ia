from datetime import datetime

from sqlalchemy import Column, ForeignKey, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow
from app.db.types import UTCDateTime
from app.models.enums import Provider, db_enum
from app.models.reference import Participant

# Keep only a short excerpt: copyright, and we link to the source for the full text.
NEWS_SUMMARY_MAX_CHARS = 500

news_item_participant = Table(
    "news_item_participant",
    Base.metadata,
    Column("news_item_id", ForeignKey("news_item.id", ondelete="CASCADE"), primary_key=True),
    Column("participant_id", ForeignKey("participant.id", ondelete="CASCADE"), primary_key=True),
)


class NewsItem(Base):
    __tablename__ = "news_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Provider] = mapped_column(db_enum(Provider, "provider"))
    publisher: Mapped[str | None] = mapped_column(String(120))  # e.g. "Marca"
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of normalized URL
    title: Mapped[str] = mapped_column(String(400))
    summary: Mapped[str | None] = mapped_column(String(NEWS_SUMMARY_MAX_CHARS))
    language: Mapped[str | None] = mapped_column(String(5))
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_run.id"))

    participants: Mapped[list[Participant]] = relationship(secondary=news_item_participant)
