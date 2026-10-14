"""The schema itself enforces UTC, exact decimals and basic integrity rules."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError, StatementError

from app.models import Bet, Competition, Event, NewsItem, OddSnapshot, Participant, User
from app.models.enums import BetStatus, Market, Provider, Selection, Sport

KICKOFF = datetime(2026, 10, 18, 19, 0, tzinfo=UTC)


@pytest.fixture
def event(db):
    comp = Competition(name="LaLiga", sport=Sport.FOOTBALL, country_code="ES")
    home = Participant(name="Real Betis", sport=Sport.FOOTBALL, country_code="ES")
    away = Participant(name="Sevilla FC", sport=Sport.FOOTBALL, country_code="ES")
    ev = Event(competition=comp, home=home, away=away, starts_at=KICKOFF)
    db.add(ev)
    db.commit()
    return ev


@pytest.fixture
def user(db):
    u = User(email="dev@example.com")
    db.add(u)
    db.commit()
    return u


def _bet(user_id: int, **overrides) -> Bet:
    fields = dict(
        user_id=user_id,
        event_label="Real Betis - Sevilla FC",
        market=Market.H2H,
        selection=Selection.HOME,
        odds_taken=Decimal("2.150"),
        stake=Decimal("10.00"),
        currency="EUR",
        placed_at=KICKOFF - timedelta(hours=2),
    )
    fields.update(overrides)
    return Bet(**fields)


def test_datetimes_round_trip_as_aware_utc(db, event):
    madrid_summer = timezone(timedelta(hours=2))
    event.starts_at = datetime(2026, 10, 18, 21, 0, tzinfo=madrid_summer)
    db.commit()
    db.expire_all()

    reloaded = db.get(Event, event.id)
    assert reloaded.starts_at.tzinfo is not None
    assert reloaded.starts_at.utcoffset() == timedelta(0)
    assert reloaded.starts_at == datetime(2026, 10, 18, 19, 0, tzinfo=UTC)


def test_naive_datetime_is_rejected(db, event):
    event.starts_at = datetime(2026, 10, 18, 19, 0)  # noqa: DTZ001 - on purpose
    with pytest.raises(StatementError, match="Naive datetimes"):
        db.commit()


def test_money_round_trips_exactly(db, user):
    db.add(_bet(user.id, stake=Decimal("1234567.89"), odds_taken=Decimal("1.875")))
    db.commit()
    db.expire_all()

    bet = db.query(Bet).one()
    assert bet.stake == Decimal("1234567.89")
    assert isinstance(bet.stake, Decimal)
    assert bet.odds_taken == Decimal("1.875")


def test_float_money_is_rejected(db, user):
    db.add(_bet(user.id, stake=10.1))
    with pytest.raises(StatementError, match="Floats are not allowed"):
        db.commit()


def test_odd_price_must_be_greater_than_one(db, event):
    db.add(
        OddSnapshot(
            event_id=event.id,
            source=Provider.THE_ODDS_API,
            bookmaker="example_book",
            market=Market.H2H,
            selection=Selection.HOME,
            price=Decimal("0.950"),
            captured_at=KICKOFF - timedelta(hours=5),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_settled_bet_requires_settled_at(db, user):
    db.add(_bet(user.id, status=BetStatus.WON, payout=Decimal("21.50")))
    with pytest.raises(IntegrityError):
        db.commit()


def test_event_sides_must_differ(db, event):
    event.away_participant_id = event.home_participant_id
    with pytest.raises(IntegrityError):
        db.commit()


def test_foreign_keys_enforced(db):
    db.add(_bet(user_id=9999))
    with pytest.raises(IntegrityError):
        db.commit()


def test_news_url_hash_is_unique(db):
    def item() -> NewsItem:
        return NewsItem(
            source=Provider.NEWSAPI,
            url="https://example.com/a",
            url_hash="a" * 64,
            title="t",
            published_at=KICKOFF,
        )

    db.add(item())
    db.commit()
    db.add(item())
    with pytest.raises(IntegrityError):
        db.commit()


def test_user_repr_hides_personal_data(db):
    u = User(email="someone@example.com")
    assert "someone" not in repr(u)
