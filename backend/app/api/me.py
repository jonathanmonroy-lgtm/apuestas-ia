"""The current user's tracker settings (INTERNAL MODE: the single local user)."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.bets import Currency, Payout
from app.api.deps import current_user
from app.db.session import get_db
from app.models import User

router = APIRouter(prefix="/api/me", tags=["me"])


class BankrollIn(BaseModel):
    amount: Payout  # >= 0, 2 decimals
    currency: Currency


class BankrollOut(BaseModel):
    starting_bankroll: Decimal | None
    currency: str | None


@router.get("/bankroll", response_model=BankrollOut)
def get_bankroll(user: Annotated[User, Depends(current_user)]) -> BankrollOut:
    return BankrollOut(starting_bankroll=user.starting_bankroll, currency=user.bankroll_currency)


@router.put("/bankroll", response_model=BankrollOut)
def set_bankroll(
    body: BankrollIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BankrollOut:
    user.starting_bankroll = body.amount
    user.bankroll_currency = body.currency
    db.commit()
    return BankrollOut(starting_bankroll=user.starting_bankroll, currency=user.bankroll_currency)
