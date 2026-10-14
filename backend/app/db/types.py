"""Portable column types (SQLite for prototyping, Postgres as target).

- UTCDateTime: always timezone-aware UTC in Python; `timestamptz` on Postgres.
  SQLite has no tz support, so we store naive UTC and re-attach tzinfo on read.
- Money / DecimalType: NUMERIC on Postgres. SQLite would store NUMERIC as REAL
  (float, loses precision), so there we store the exact decimal as TEXT.
- JSONType: JSONB on Postgres, JSON elsewhere.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

JSONType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; use timezone-aware UTC.")
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class DecimalType(TypeDecorator[Decimal]):
    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int, scale: int) -> None:
        super().__init__()
        self.precision = precision
        self.scale = scale

    def __repr__(self) -> str:  # rendered verbatim by Alembic autogenerate
        return f"DecimalType({self.precision}, {self.scale})"

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(self.precision + 2))
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | Decimal | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("Floats are not allowed for decimal columns; use Decimal.")
        value = Decimal(value)
        if dialect.name == "sqlite":
            return str(value)
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))


def Money() -> DecimalType:
    """Amounts of money: 14 digits, 2 decimals. Always paired with a currency column."""
    return DecimalType(14, 2)


def Price() -> DecimalType:
    """Decimal odds, e.g. 1.875."""
    return DecimalType(10, 3)


def Probability() -> DecimalType:
    return DecimalType(7, 6)
