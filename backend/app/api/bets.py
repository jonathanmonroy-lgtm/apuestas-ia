"""The user's own bet log (CRUD) and tracker metrics.

These are the user's records, not our data or analysis: we never place bets. Money is
Decimal end to end (JSON carries it as strings, never floats). Logging a bet respects
the user's responsible-gambling settings: an active self-exclusion blocks it (403) and
a stake that would exceed an active spending limit is refused (409).
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import current_user
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Bet, Event, User
from app.models.enums import BetStatus, Market, Selection
from app.services.bet_metrics import BetRow, compute_metrics, default_payout, money
from app.services.responsible_gambling import active_self_exclusion, spending_limit_breach

router = APIRouter(prefix="/api", tags=["bets"])


def _currency(value: str) -> str:
    value = value.strip().upper()
    if len(value) != 3 or not value.isalpha():
        raise ValueError("currency must be a 3-letter ISO 4217 code")
    return value


def _cents(value: Decimal) -> Decimal:
    if value != value.quantize(Decimal("0.01")):
        raise ValueError("at most 2 decimals")
    return value


def _odds(value: Decimal) -> Decimal:
    if value != value.quantize(Decimal("0.001")):
        raise ValueError("at most 3 decimals")
    return value


Currency = Annotated[str, AfterValidator(_currency)]
Stake = Annotated[Decimal, Field(gt=0, max_digits=14), AfterValidator(_cents)]
Odds = Annotated[Decimal, Field(gt=1, lt=10000), AfterValidator(_odds)]
Payout = Annotated[Decimal, Field(ge=0, max_digits=14), AfterValidator(_cents)]


class BetCreate(BaseModel):
    event_id: int | None = None
    event_label: str | None = Field(None, max_length=200)
    market: Market = Market.H2H
    selection: Selection
    odds_taken: Odds
    stake: Stake
    currency: Currency
    placed_at: AwareDatetime | None = None  # default: now
    notes: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _needs_event(self) -> "BetCreate":
        if self.event_id is None and not (self.event_label and self.event_label.strip()):
            raise ValueError("event_id or event_label is required")
        return self


class BetUpdate(BaseModel):
    """Financial fields can only change while the bet is pending (reopen it first)."""

    event_label: str | None = Field(None, max_length=200)
    selection: Selection | None = None
    odds_taken: Odds | None = None
    stake: Stake | None = None
    placed_at: AwareDatetime | None = None
    notes: str | None = Field(None, max_length=2000)
    status: BetStatus | None = None
    # Optional override for a settled bet (cash-out, bookmaker rules); default from odds.
    payout: Payout | None = None
    settled_at: AwareDatetime | None = None


class BetOut(BaseModel):
    id: int
    event_id: int | None
    event_label: str
    event_starts_at: datetime | None
    market: Market
    selection: Selection
    odds_taken: Decimal
    stake: Decimal
    currency: str
    status: BetStatus
    payout: Decimal | None
    profit: Decimal | None
    placed_at: datetime
    settled_at: datetime | None
    notes: str | None


class BetList(BaseModel):
    total: int
    items: list[BetOut]


def _out(b: Bet) -> BetOut:
    return BetOut(
        id=b.id,
        event_id=b.event_id,
        event_label=b.event_label,
        event_starts_at=b.event.starts_at if b.event else None,
        market=b.market,
        selection=b.selection,
        odds_taken=b.odds_taken,
        stake=b.stake,
        currency=b.currency,
        status=b.status,
        payout=b.payout,
        profit=money(b.payout - b.stake) if b.payout is not None else None,
        placed_at=b.placed_at,
        settled_at=b.settled_at,
        notes=b.notes,
    )


def _get_own(db: Session, user: User, bet_id: int) -> Bet:
    bet = db.get(Bet, bet_id)
    if bet is None or bet.user_id != user.id:
        raise HTTPException(404, "bet not found")
    return bet


DbDep = Annotated[Session, Depends(get_db)]
UserDep = Annotated[User, Depends(current_user)]


@router.post("/bets", response_model=BetOut, status_code=201)
def create_bet(body: BetCreate, db: DbDep, user: UserDep) -> BetOut:
    now = datetime.now(UTC)
    if active_self_exclusion(db, user.id, now):
        raise HTTPException(403, "autoexclusión activa: no se pueden registrar apuestas")
    breach = spending_limit_breach(db, user.id, body.stake, body.currency, now)
    if breach:
        raise HTTPException(
            409,
            f"supera tu límite {breach.period.value} de {breach.limit} {breach.currency} "
            f"(llevas {breach.already_staked})",
        )
    label = body.event_label.strip() if body.event_label else None
    if body.event_id is not None:
        event = db.get(Event, body.event_id)
        if event is None:
            raise HTTPException(422, "event_id does not exist")
        label = label or f"{event.home.name} - {event.away.name}"
    bet = Bet(
        user_id=user.id,
        event_id=body.event_id,
        event_label=label,
        market=body.market,
        selection=body.selection,
        odds_taken=body.odds_taken,
        stake=body.stake,
        currency=body.currency,
        status=BetStatus.PENDING,
        placed_at=body.placed_at or now,
        notes=body.notes,
    )
    db.add(bet)
    db.commit()
    return _out(bet)


@router.get("/bets", response_model=BetList)
def list_bets(
    db: DbDep,
    user: UserDep,
    status: BetStatus | None = None,
    currency: str | None = None,
    date_from: date | None = Query(None, description="placed on/after (UTC date)"),  # noqa: B008
    date_to: date | None = Query(None, description="placed on/before (UTC date)"),  # noqa: B008
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> BetList:
    where = [Bet.user_id == user.id]
    if status is not None:
        where.append(Bet.status == status)
    if currency:
        where.append(Bet.currency == currency.upper())
    if date_from:
        where.append(Bet.placed_at >= datetime.combine(date_from, time.min, UTC))
    if date_to:
        where.append(Bet.placed_at < datetime.combine(date_to + timedelta(days=1), time.min, UTC))
    total = db.scalar(select(func.count()).select_from(Bet).where(*where)) or 0
    bets = db.scalars(
        select(Bet)
        .options(joinedload(Bet.event))
        .where(*where)
        .order_by(Bet.placed_at.desc(), Bet.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return BetList(total=total, items=[_out(b) for b in bets])


@router.get("/bets/{bet_id}", response_model=BetOut)
def get_bet(bet_id: int, db: DbDep, user: UserDep) -> BetOut:
    return _out(_get_own(db, user, bet_id))


@router.patch("/bets/{bet_id}", response_model=BetOut)
def update_bet(bet_id: int, body: BetUpdate, db: DbDep, user: UserDep) -> BetOut:
    bet = _get_own(db, user, bet_id)
    changes = body.model_dump(exclude_unset=True)
    financial = {"selection", "odds_taken", "stake", "placed_at"} & changes.keys()
    target = changes.get("status", bet.status)
    if financial and (bet.status is not BetStatus.PENDING or target is not BetStatus.PENDING):
        raise HTTPException(409, "reopen the bet (status=pending) before editing it")
    for field in ("event_label", "notes", *financial):
        if field in changes and changes[field] is not None:
            setattr(bet, field, changes[field])

    if "status" in changes or "payout" in changes or "settled_at" in changes:
        if target is BetStatus.PENDING:
            if changes.get("payout") is not None:
                raise HTTPException(422, "a pending bet has no payout")
            bet.status, bet.payout, bet.settled_at = BetStatus.PENDING, None, None
        else:
            payout = changes.get("payout")
            if payout is not None and target is not BetStatus.WON:
                raise HTTPException(422, "a manual payout only applies to won bets")
            bet.status = target
            bet.payout = (
                payout if payout is not None else default_payout(target, bet.stake, bet.odds_taken)
            )
            bet.settled_at = changes.get("settled_at") or bet.settled_at or datetime.now(UTC)
    db.commit()
    return _out(bet)


@router.delete("/bets/{bet_id}", status_code=204)
def delete_bet(bet_id: int, db: DbDep, user: UserDep) -> Response:
    db.delete(_get_own(db, user, bet_id))
    db.commit()
    return Response(status_code=204)


# --- metrics ---


class SeriesPointOut(BaseModel):
    bet_id: int
    settled_at: datetime
    profit: Decimal
    cumulative_profit: Decimal
    bankroll: Decimal | None


class MetricsOut(BaseModel):
    kind: Literal["user_records"] = "user_records"
    currency: str
    definitions: dict[str, str]
    n_bets: int
    n_pending: int
    n_won: int
    n_lost: int
    n_void: int
    staked: Decimal
    returned: Decimal
    profit: Decimal
    roi: Decimal | None
    win_rate: Decimal | None
    avg_stake: Decimal | None
    pending_stake: Decimal
    starting_bankroll: Decimal | None
    bankroll: Decimal | None
    available: Decimal | None
    series: list[SeriesPointOut]


DEFINITIONS = {
    "roi": "beneficio / total apostado en apuestas resueltas (las anuladas no cuentan)",
    "win_rate": "ganadas / (ganadas + perdidas)",
    "avg_stake": "stake medio de las apuestas resueltas (sin anuladas)",
    "bankroll": "bankroll inicial declarado + beneficio",
    "available": "bankroll menos lo apostado en apuestas pendientes",
    "series": "evolución por apuesta resuelta, en orden de liquidación",
}


@router.get("/bets-metrics", response_model=MetricsOut)
def bet_metrics(
    db: DbDep,
    user: UserDep,
    settings: Annotated[Settings, Depends(get_settings)],
    currency: str | None = None,
) -> MetricsOut:
    currency = _currency(currency or settings.default_currency)
    bets = db.scalars(select(Bet).where(Bet.user_id == user.id, Bet.currency == currency)).all()
    start = user.starting_bankroll if user.bankroll_currency == currency else None
    m = compute_metrics(
        (BetRow(b.id, b.status, b.stake, b.payout, b.settled_at) for b in bets), start
    )
    return MetricsOut(
        currency=currency,
        definitions=DEFINITIONS,
        **{k: v for k, v in m.__dict__.items() if k != "series"},
        series=[SeriesPointOut(**p.__dict__) for p in m.series],
    )
