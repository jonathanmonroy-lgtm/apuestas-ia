from datetime import UTC, datetime

from app.models.enums import Provider, QuotaPeriod
from app.services.quota import QuotaBudget, QuotaPolicy, next_reset

DAY1 = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
DAY2 = datetime(2026, 10, 15, 0, 5, tzinfo=UTC)


def budget(db, limit: int = 2) -> QuotaBudget:
    return QuotaBudget(db, Provider.FOOTBALL_DATA, QuotaPolicy(QuotaPeriod.DAY, limit))


def test_refuses_over_budget_and_rolls_over_next_day(db):
    b = budget(db)
    b.record_call(now=DAY1)
    b.record_call(now=DAY1)
    db.commit()

    allowed, reason = b.can_spend(now=DAY1)
    assert not allowed
    assert "2/2" in reason
    assert b.can_spend(now=DAY2)[0]


def test_provider_reported_zero_pauses_until_reset(db):
    b = budget(db, limit=100)
    b.record_call(remaining=0, now=DAY1)
    db.commit()

    allowed, reason = b.can_spend(now=DAY1)
    assert not allowed
    assert "paused" in reason


def test_monthly_reset_crosses_year():
    assert next_reset(datetime(2026, 12, 20, tzinfo=UTC), QuotaPeriod.MONTH) == datetime(
        2027, 1, 1, tzinfo=UTC
    )
