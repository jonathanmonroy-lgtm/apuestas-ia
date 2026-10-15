"""Reference data: competitions, seasons, participants and cross-source ID mapping."""

from datetime import date

from sqlalchemy import Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import EntityType, Provider, Sport, db_enum


class Competition(TimestampMixin, Base):
    __tablename__ = "competition"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    sport: Mapped[Sport] = mapped_column(db_enum(Sport, "sport"))
    country_code: Mapped[str | None] = mapped_column(String(2))  # ISO 3166-1 alpha-2
    # Provider's country name: also covers what has no ISO alpha-2 code (England, World).
    country_name: Mapped[str | None] = mapped_column(String(60))

    seasons: Mapped[list["Season"]] = relationship(back_populates="competition")

    __table_args__ = (UniqueConstraint("sport", "country_code", "name"),)


class Season(TimestampMixin, Base):
    __tablename__ = "season"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id"))
    name: Mapped[str] = mapped_column(String(20))  # e.g. "2026-27"
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    competition: Mapped[Competition] = relationship(back_populates="seasons")

    __table_args__ = (UniqueConstraint("competition_id", "name"),)


class Participant(TimestampMixin, Base):
    """A team (or player, for individual sports)."""

    __tablename__ = "participant"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    short_name: Mapped[str | None] = mapped_column(String(40))
    sport: Mapped[Sport] = mapped_column(db_enum(Sport, "sport"))
    country_code: Mapped[str | None] = mapped_column(String(2))


class ExternalRef(TimestampMixin, Base):
    """Maps (source, external_id) to our internal id.

    Each provider names/identifies the same team differently. For providers without
    stable ids (The Odds API identifies teams by name), external_id is the provider's
    name string, so the mapping doubles as an alias table for event matching.
    """

    __tablename__ = "external_ref"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Provider] = mapped_column(db_enum(Provider, "provider"))
    entity_type: Mapped[EntityType] = mapped_column(db_enum(EntityType, "entity_type"))
    external_id: Mapped[str] = mapped_column(String(200))
    entity_id: Mapped[int]
    raw_name: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("source", "entity_type", "external_id"),
        Index("ix_external_ref_entity", "entity_type", "entity_id"),
    )
