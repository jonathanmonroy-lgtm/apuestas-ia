"""Regression tests for qa-reviewer findings #5, #6 and the inference label on Phase 4."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import IngestionRun
from app.models.enums import IngestionStatus, Provider
from app.services.ingestion.news_job import coverage_gap, run_news_ingestion
from app.services.ingestion.retention import purge_raw_payloads
from app.services.news_provider.base import NewsBatch
from tests.test_news_ingestion import (  # noqa: F401 - fixtures
    POLICY,
    FakeNewsProvider,
    client,
    load_news,
    raw,
    sf,
    stored,
    teams,
)

T = datetime(2026, 10, 13, 21, 0, tzinfo=UTC)


def batch(page_count: int, oldest: datetime | None) -> NewsBatch:
    return NewsBatch(
        source=Provider.NEWSAPI, language="es", page_count=page_count,
        oldest_in_page=oldest, articles=[],
    )  # fmt: skip


# --- #5: a real gap, not a warning every hour ---


def test_full_page_overlapping_what_we_have_is_not_a_gap():
    assert coverage_gap(batch(100, T - timedelta(hours=1)), newest_stored=T, page_size=100) is None


def test_partial_page_is_never_a_gap():
    assert coverage_gap(batch(37, T + timedelta(hours=3)), newest_stored=T, page_size=100) is None


def test_full_page_that_does_not_reach_back_is_a_gap():
    gap = coverage_gap(batch(100, T + timedelta(hours=3)), newest_stored=T, page_size=100)
    assert gap == (T, T + timedelta(hours=3))


def test_page_count_is_before_filtering():
    """[Removed] articles are filtered, but the PAGE was still full."""
    payload = load_news()
    removed = [dict(payload["articles"][i], title="[Removed]") for i in (0, 1)]
    payload["articles"] = [*(payload["articles"] * 4)[:98], *removed]
    parsed = FakeNewsProvider(payload).normalize(raw(payload))
    assert parsed.page_count == 100
    assert len(parsed.articles) == 98


# --- #6: third-party text not kept in raw_payload, and raw is purged ---


def test_raw_payload_kept_without_article_content(sf, teams):  # noqa: F811
    outcome = run_news_ingestion(sf, FakeNewsProvider(), policy=POLICY)
    with sf() as s:
        kept = s.get_one(IngestionRun, outcome.run_id).raw_payload
    assert len(kept["articles"]) == 27
    assert all("content" not in a and "urlToImage" not in a for a in kept["articles"])
    assert all("title" in a and "url" in a for a in kept["articles"])  # still auditable


def test_old_raw_payloads_are_purged(sf, teams):  # noqa: F811
    outcome = run_news_ingestion(sf, FakeNewsProvider(), policy=POLICY)
    assert purge_raw_payloads(sf, days=30) == 0  # fresh: kept
    purged = purge_raw_payloads(sf, days=30, now=datetime.now(UTC) + timedelta(days=31))
    assert purged >= 1
    with sf() as s:
        run = s.get_one(IngestionRun, outcome.run_id)
        assert run.raw_payload is None
        assert run.status is IngestionStatus.SUCCESS  # the run record stays


# --- inference label on the API ---


def test_team_assignment_is_labelled_as_inference(client, stored):  # noqa: F811
    item = client.get("/api/news", params={"limit": 1}).json()["items"][0]
    assert item["kind"] == "news"
    assert item["teams_assignment"] == "inferred_by_rules"


def test_runs_listed(sf, teams):  # noqa: F811
    run_news_ingestion(sf, FakeNewsProvider(), policy=POLICY)
    with sf() as s:
        assert s.scalars(select(IngestionRun.job)).all() == ["news"]
