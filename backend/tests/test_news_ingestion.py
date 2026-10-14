import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import Event, IngestionRun, NewsItem, Participant
from app.models.enums import IngestionStatus, QuotaPeriod
from app.services.errors import AuthError, ProviderError, QuotaExhaustedError
from app.services.fixtures_provider.football_data import FootballDataProvider
from app.services.http import ProviderHttpClient, RawResponse
from app.services.ingestion.fixtures_job import upsert_batch
from app.services.ingestion.news_job import run_news_ingestion
from app.services.ingestion.urls import canonical_url, url_hash
from app.services.news_provider.newsapi import BASE_URL, NewsApiProvider, classify_body
from app.services.quota import QuotaPolicy

FIXTURES = Path(__file__).parent / "fixtures"
NEWS = FIXTURES / "newsapi/everything_laliga_es_real.json"
POLICY = QuotaPolicy(QuotaPeriod.DAY, 50)
# Articles in the real fixture that are about LaLiga teams (see test_news_classifier.py)
EXPECTED_STORED = 11


def load_news() -> dict:
    return json.loads(NEWS.read_text(encoding="utf-8"))


def raw(payload) -> RawResponse:
    return RawResponse(payload=payload, url="https://x", http_status=200, headers={}, attempts=1)


class FakeNewsProvider(NewsApiProvider):
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else load_news()
        self.calls = 0

    def fetch_news(self):
        self.calls += 1
        return raw(self.payload)

    def close(self):
        pass


@pytest.fixture
def sf(migrated_engine):
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def teams(sf):
    """Real matchday 1 of LaLiga 2026-27: all 20 teams in the calendar."""
    payload = json.loads(
        (FIXTURES / "football_data/matches_pd_2026_real_matchday1.json").read_text(encoding="utf-8")
    )
    with sf() as s, s.begin():
        upsert_batch(s, FootballDataProvider("k").normalize(raw(payload)))


def count(sf, model) -> int:
    with sf() as s:
        return s.scalar(select(func.count()).select_from(model))


# --- URL canonicalisation / hash ---


@pytest.mark.parametrize(
    "variant",
    [
        "https://www.marca.com/futbol/a.html",
        "http://marca.com/futbol/a.html",
        "https://MARCA.com/futbol/a.html/",
        "https://www.marca.com/futbol/a.html?utm_source=x&utm_medium=y#comentarios",
        "https://www.marca.com/futbol/a.html?fbclid=abc",
    ],
)
def test_url_variants_hash_the_same(variant):
    assert url_hash(variant) == url_hash("https://marca.com/futbol/a.html")


def test_real_query_params_are_kept_and_sorted():
    assert canonical_url("https://x.com/n?id=2&page=1") == "https://x.com/n?id=2&page=1"
    assert canonical_url("https://x.com/n?page=1&id=2") == "https://x.com/n?id=2&page=1"
    assert url_hash("https://x.com/n?id=1") != url_hash("https://x.com/n?id=2")


# --- provider ---


def test_normalize_real_payload_and_skip_removed():
    payload = load_news()
    payload["articles"].append(dict(payload["articles"][0], title="[Removed]"))
    batch = NewsApiProvider("k").normalize(raw(payload))
    assert len(batch.articles) == 27
    assert batch.articles[0].published_at.tzinfo is not None


def test_real_error_bodies():
    """Recorded 2026-10-14: a bogus key, and asking for page 2 on the free plan."""
    invalid = {"status": "error", "code": "apiKeyInvalid",
               "message": "Your API key is invalid or incorrect."}  # fmt: skip
    capped = {"status": "error", "code": "maximumResultsReached",
              "message": "Developer accounts are limited to a max of 100 results."}  # fmt: skip
    rate = {"status": "error", "code": "rateLimited", "message": "You have been rate limited."}
    assert isinstance(classify_body(httpx.Response(401), invalid), AuthError)
    err = classify_body(httpx.Response(426), capped)
    assert type(err) is ProviderError
    assert isinstance(classify_body(httpx.Response(429), rate), QuotaExhaustedError)


def test_request_shape_and_key_in_header_only():
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=load_news())

    client = ProviderHttpClient(
        source="newsapi", base_url=BASE_URL, auth_headers={"X-Api-Key": "SECRET"},
        classify_body=classify_body, sleep=lambda s: None, transport=httpx.MockTransport(handler),
    )  # fmt: skip
    result = NewsApiProvider("SECRET", client=client).fetch_news()
    params = dict(seen[0].url.params)
    assert params["language"] == "es"
    assert params["searchIn"] == "title,description"
    assert params["pageSize"] == "100"
    assert '"Real Madrid"' in params["q"] and " OR " in params["q"]
    assert seen[0].headers["X-Api-Key"] == "SECRET"
    assert "SECRET" not in result.url


# --- job ---


def test_only_laliga_articles_are_stored_with_their_teams(sf, teams):
    outcome = run_news_ingestion(sf, FakeNewsProvider(), policy=POLICY)

    assert outcome.status is IngestionStatus.SUCCESS
    assert outcome.records_written == EXPECTED_STORED
    with sf() as s:
        item = s.scalar(select(NewsItem).where(NewsItem.url.contains("isco-apunta-champions")))
        assert sorted(p.name for p in item.participants) == ["Osasuna", "Real Betis"]
        assert item.publisher == "Www.abc.es"
        assert item.published_at.tzinfo is not None
        titles = s.scalars(select(NewsItem.title)).all()
        assert not any("Euroliga" in t or "Hilton" in t for t in titles)


def test_rerun_and_tracking_copies_are_deduplicated(sf, teams):
    run_news_ingestion(sf, FakeNewsProvider(), policy=POLICY)
    payload = load_news()
    for a in payload["articles"]:
        a["url"] = a["url"] + "?utm_source=twitter"
    second = run_news_ingestion(sf, FakeNewsProvider(payload), policy=POLICY)
    assert second.records_written == 0
    assert count(sf, NewsItem) == EXPECTED_STORED


def test_summary_is_short_and_clean(sf, teams):
    payload = load_news()
    pleno = next(a for a in payload["articles"] if "pleno-espanol" in a["url"])
    assert pleno["description"].startswith("<![CDATA[")  # real feed quirk
    pleno["description"] += " relleno" * 200
    run_news_ingestion(sf, FakeNewsProvider(payload), policy=POLICY)
    with sf() as s:
        item = s.scalar(select(NewsItem).where(NewsItem.url.contains("pleno-espanol")))
        assert len(item.summary) <= 500
        assert "CDATA" not in item.summary and "<p>" not in item.summary


def test_teams_not_in_calendar_are_not_invented(sf):
    outcome = run_news_ingestion(sf, FakeNewsProvider(), policy=POLICY)
    assert outcome.status is IngestionStatus.SUCCESS
    assert count(sf, NewsItem) == 0
    assert count(sf, Participant) == 0


def test_duplicates_within_one_batch(sf, teams):
    payload = load_news()
    payload["articles"].append(copy.deepcopy(payload["articles"][2]))  # same Barça article
    outcome = run_news_ingestion(sf, FakeNewsProvider(payload), policy=POLICY)
    assert outcome.records_written == EXPECTED_STORED


def test_daily_budget_skips_without_calling(sf, teams):
    provider = FakeNewsProvider()
    policy = QuotaPolicy(QuotaPeriod.DAY, 1)
    run_news_ingestion(sf, provider, policy=policy)
    outcome = run_news_ingestion(sf, provider, policy=policy)
    assert outcome.status is IngestionStatus.SKIPPED_NO_BUDGET
    assert provider.calls == 1
    with sf() as s:
        assert s.get_one(IngestionRun, outcome.run_id).status is IngestionStatus.SKIPPED_NO_BUDGET


# --- API ---


@pytest.fixture
def client(sf):
    def override_db():
        with sf() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: Settings()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def stored(sf, teams):
    run_news_ingestion(sf, FakeNewsProvider(), policy=POLICY)


def test_api_lists_news_newest_first_with_delay_notice(client, stored):
    body = client.get("/api/news").json()
    assert len(body["items"]) == EXPECTED_STORED
    published = [i["published_at"] for i in body["items"]]
    assert published == sorted(published, reverse=True)
    assert body["items"][0]["kind"] == "news"
    assert body["data_as_of"]["provider_delay_hours"] == 24
    assert body["data_as_of"]["checked_at"] is not None
    assert "retraso" in body["notice"]


def test_api_filters_by_team_and_by_event(client, sf, stored):
    with sf() as s:
        villarreal = s.scalar(select(Participant).where(Participant.name == "Villarreal"))
        event = s.scalar(
            select(Event).where(
                (Event.home_participant_id == villarreal.id)
                | (Event.away_participant_id == villarreal.id)
            )
        )
    by_team = client.get("/api/news", params={"participant_id": villarreal.id}).json()["items"]
    assert len(by_team) == 3  # Villarreal x2 + the "pleno español" article
    assert all(villarreal.id in [t["id"] for t in i["teams"]] for i in by_team)
    by_event = client.get("/api/news", params={"event_id": event.id}).json()["items"]
    assert {i["id"] for i in by_team} <= {i["id"] for i in by_event}
    assert client.get("/api/news", params={"event_id": 999999}).status_code == 404


def test_api_limit_validation(client):
    assert client.get("/api/news", params={"limit": 0}).status_code == 422
    assert client.get("/api/news", params={"limit": 101}).status_code == 422


def test_published_dates_are_utc(client, stored):
    item = client.get("/api/news", params={"limit": 1}).json()["items"][0]
    assert item["published_at"].endswith("Z")
    assert datetime.fromisoformat(item["published_at"]).tzinfo == UTC
