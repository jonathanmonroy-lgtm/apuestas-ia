"""The current user's settings (INTERNAL MODE: the single local user): starting bankroll
and responsible-gambling controls (spending limits, self-exclusion).

Responsible gambling is a product rule, not an option: the user can always see their
limits and how much is left, lower a limit instantly, and self-exclude. Raising a limit
waits a cooling-off period; a self-exclusion can't be shortened or lifted through the API.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.bets import Currency, Payout
from app.api.deps import current_user
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import SelfExclusion, User
from app.models.enums import LimitPeriod
from app.services.responsible_gambling import (
    active_self_exclusion,
    limit_status,
    set_spending_limit,
)

router = APIRouter(prefix="/api/me", tags=["me"])

UserDep = Annotated[User, Depends(current_user)]
DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# --- bankroll ---


class BankrollIn(BaseModel):
    amount: Payout  # >= 0, 2 decimals
    currency: Currency


class BankrollOut(BaseModel):
    starting_bankroll: Decimal | None
    currency: str | None


@router.get("/bankroll", response_model=BankrollOut)
def get_bankroll(user: UserDep) -> BankrollOut:
    return BankrollOut(starting_bankroll=user.starting_bankroll, currency=user.bankroll_currency)


@router.put("/bankroll", response_model=BankrollOut)
def set_bankroll(body: BankrollIn, user: UserDep, db: DbDep) -> BankrollOut:
    user.starting_bankroll = body.amount
    user.bankroll_currency = body.currency
    db.commit()
    db.refresh(user)
    return BankrollOut(starting_bankroll=user.starting_bankroll, currency=user.bankroll_currency)


# --- spending limits ---


class LimitIn(BaseModel):
    period: LimitPeriod
    amount: Payout
    currency: Currency


class LimitOut(BaseModel):
    period: LimitPeriod
    amount: Decimal
    currency: str
    used: Decimal
    remaining: Decimal
    period_ends_at: datetime
    pending_amount: Decimal | None
    pending_effective_from: datetime | None


class LimitsOut(BaseModel):
    currency: str
    cooling_off_hours: int
    note: str
    limits: list[LimitOut]


def _limits(db: Session, user: User, currency: str, settings: Settings) -> LimitsOut:
    now = datetime.now(UTC)
    return LimitsOut(
        currency=currency,
        cooling_off_hours=settings.limit_increase_cooling_off_hours,
        note=(
            "Bajar un límite es inmediato; subirlo tarda "
            f"{settings.limit_increase_cooling_off_hours} h. Los periodos son naturales en UTC."
        ),
        limits=[LimitOut(**s.__dict__) for s in limit_status(db, user.id, currency, now)],
    )


@router.get("/limits", response_model=LimitsOut)
def get_limits(
    user: UserDep, db: DbDep, settings: SettingsDep, currency: str | None = None
) -> LimitsOut:
    return _limits(db, user, (currency or settings.default_currency).upper(), settings)


@router.put("/limits", response_model=LimitsOut)
def put_limit(body: LimitIn, user: UserDep, db: DbDep, settings: SettingsDep) -> LimitsOut:
    set_spending_limit(
        db,
        user.id,
        body.period,
        body.amount,
        body.currency,
        datetime.now(UTC),
        timedelta(hours=settings.limit_increase_cooling_off_hours),
    )
    db.commit()
    return _limits(db, user, body.currency, settings)


# --- self-exclusion ---


class SelfExclusionIn(BaseModel):
    days: int | None = Field(None, ge=1, le=3650, description="null = indefinite")


class SelfExclusionOut(BaseModel):
    active: bool
    started_at: datetime | None
    ends_at: datetime | None  # null while active = indefinite


@router.get("/self-exclusion", response_model=SelfExclusionOut)
def get_self_exclusion(user: UserDep, db: DbDep) -> SelfExclusionOut:
    ex = active_self_exclusion(db, user.id, datetime.now(UTC))
    if ex is None:
        return SelfExclusionOut(active=False, started_at=None, ends_at=None)
    return SelfExclusionOut(active=True, started_at=ex.started_at, ends_at=ex.ends_at)


@router.post("/self-exclusion", response_model=SelfExclusionOut, status_code=201)
def start_self_exclusion(body: SelfExclusionIn, user: UserDep, db: DbDep) -> SelfExclusionOut:
    now = datetime.now(UTC)
    current = active_self_exclusion(db, user.id, now)
    ends_at = now + timedelta(days=body.days) if body.days else None
    if current is not None:
        # Only extending is allowed, never shortening.
        shorter = current.ends_at is None or (ends_at is not None and ends_at <= current.ends_at)
        if shorter:
            raise HTTPException(409, "ya hay una autoexclusión activa igual o más larga")
    db.add(SelfExclusion(user_id=user.id, started_at=now, ends_at=ends_at))
    db.commit()
    return SelfExclusionOut(active=True, started_at=now, ends_at=ends_at)
