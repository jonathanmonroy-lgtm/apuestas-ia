"""Users and responsible-gambling controls.

`birth_date` is sensitive personal data: never log it, never return it from the API
(only derived `is_adult`), and keep it out of reprs. Encryption at rest is a pending
decision that depends on jurisdiction (GDPR / local data-protection law).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.db.types import Money, UTCDateTime
from app.models.enums import LimitPeriod, db_enum


class User(TimestampMixin, Base):
    __tablename__ = "user_account"  # "user" is reserved in Postgres

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    # Auth strategy (managed vs own JWT) is still pending; nullable on purpose.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    country_code: Mapped[str | None] = mapped_column(String(2))
    birth_date: Mapped[date | None] = mapped_column(Date)
    age_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    age_verification_method: Mapped[str | None] = mapped_column(String(40))
    terms_version_accepted: Mapped[str | None] = mapped_column(String(20))
    terms_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # The user's own declared starting bankroll for the bet tracker (optional).
    starting_bankroll: Mapped[Decimal | None] = mapped_column(Money())
    bankroll_currency: Mapped[str | None] = mapped_column(String(3))

    spending_limits: Mapped[list["SpendingLimit"]] = relationship(back_populates="user")
    self_exclusions: Mapped[list["SelfExclusion"]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"User(id={self.id})"


class SpendingLimit(Base):
    """Self-imposed limit, with full history (rows are superseded, never edited).

    Lowering a limit is effective immediately; raising it only after a cooling-off
    period (effective_from in the future). The cooling-off length is configurable and
    jurisdiction-dependent.
    """

    __tablename__ = "spending_limit"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    period: Mapped[LimitPeriod] = mapped_column(db_enum(LimitPeriod, "limit_period"))
    amount: Mapped[Decimal] = mapped_column(Money())
    currency: Mapped[str] = mapped_column(String(3))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime())
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    user: Mapped[User] = relationship(back_populates="spending_limits")

    __table_args__ = (
        CheckConstraint("CAST(amount AS REAL) >= 0", name="non_negative_amount"),
        Index("ix_spending_limit_active", "user_id", "period", "superseded_at"),
    )


class SelfExclusion(Base):
    __tablename__ = "self_exclusion"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # NULL = indefinite
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    user: Mapped[User] = relationship(back_populates="self_exclusions")
