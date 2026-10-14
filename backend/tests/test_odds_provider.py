import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.models.enums import Market, Provider, Selection
from app.services.errors import AuthError, FormatError, QuotaExhaustedError
from app.services.http import ProviderHttpClient, RawResponse
from app.services.odds_provider.the_odds_api import (
    BASE_URL,
    TheOddsApiProvider,
    classify_body,
    to_decimal,
)

# Real response trimmed to 3 events x 4 bookmakers
# (GET /v4/sports/soccer_spain_la_liga/odds?regions=eu&markets=h2h, recorded 2026-10-14).
REAL = Path(__file__).parent / "fixtures/the_odds_api/odds_pd_h2h_eu_real.json"


def load() -> list:
    return json.loads(REAL.read_text(encoding="utf-8"))


# When the real payloads were recorded: "already started" is judged against this, so the
# tests don't change meaning once the recorded matches are in the past.
RECORDED_AT = datetime(2026, 10, 14, 23, 5, 30, tzinfo=UTC)


def raw(payload, headers=None, fetched_at=RECORDED_AT) -> RawResponse:
    return RawResponse(
        payload=payload, url="https://x", http_status=200, headers=headers or {}, attempts=1,
        fetched_at=fetched_at,
    )  # fmt: skip


def test_normalize_real_payload():
    batch = TheOddsApiProvider("k").normalize(raw(load()), "PD")

    assert batch.source is Provider.THE_ODDS_API
    assert [e.home_name for e in batch.events] == ["Deportivo La Coruña", "Real Betis", "Barcelona"]
    first = batch.events[0]
    assert first.external_id == "19ee731602634e5c9e3106630e087464"
    assert first.away_name == "Levante"
    # 4 bookmakers x 3 outcomes; betfair's h2h_lay is NOT a back price and is dropped
    assert len(first.prices) == 12
    assert {p.market for p in first.prices} == {Market.H2H}

    pinnacle = {p.selection: p.price for p in first.prices if p.bookmaker == "pinnacle"}
    assert pinnacle == {
        Selection.HOME: Decimal("2.12"),
        Selection.DRAW: Decimal("3.52"),
        Selection.AWAY: Decimal("3.61"),
    }
    assert all(isinstance(p.price, Decimal) for p in first.prices)


def test_lay_prices_never_leak_into_back_prices():
    payload = load()
    betfair = next(b for b in payload[0]["bookmakers"] if b["key"] == "betfair_ex_eu")
    lay = next(m for m in betfair["markets"] if m["key"] == "h2h_lay")
    lay_prices = {to_decimal(o["price"]) for o in lay["outcomes"]}
    back = next(m for m in betfair["markets"] if m["key"] == "h2h")
    back_prices = {to_decimal(o["price"]) for o in back["outcomes"]}

    batch = TheOddsApiProvider("k").normalize(raw(payload), "PD")
    stored = {p.price for p in batch.events[0].prices if p.bookmaker == "betfair_ex_eu"}
    assert stored == back_prices
    assert lay_prices - back_prices  # the real lay prices differ: the check is meaningful
    assert not (lay_prices - back_prices) & stored


def test_to_decimal_keeps_the_provider_digits():
    assert to_decimal(2.12) == Decimal("2.12")
    assert to_decimal(1.1) == Decimal("1.1")  # not 1.100000000000000088817841970012523...


def test_unexpected_outcome_names_drop_that_bookmaker_only():
    payload = load()
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["name"] = "Dépor"
    batch = TheOddsApiProvider("k").normalize(raw(payload), "PD")
    assert len(batch.events[0].prices) == 9  # one bookmaker skipped, not guessed


@pytest.mark.parametrize("payload", [{"message": "x"}, [{"id": "only-id"}]])
def test_unexpected_payload_is_format_error(payload):
    with pytest.raises(FormatError):
        TheOddsApiProvider("k").normalize(raw(payload), "PD")


def test_invalid_key_real_error_is_auth_error():
    """Real 401 body recorded 2026-10-14 with a bogus key."""
    body = {
        "message": "API key is not valid. Get an API key at https://the-odds-api.com",
        "error_code": "INVALID_KEY",
        "details_url": "https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html#invalid-key",
    }
    assert isinstance(classify_body(httpx.Response(401), body), AuthError)


def test_out_of_credits_is_quota_exhausted_and_freq_limit_is_retried():
    out = {"message": "Usage quota has been reached", "error_code": "OUT_OF_USAGE_CREDITS"}
    assert isinstance(classify_body(httpx.Response(401), out), QuotaExhaustedError)
    freq = {"message": "Too many requests", "error_code": "EXCEEDED_FREQ_LIMIT"}
    assert classify_body(httpx.Response(429), freq) is None


def test_credit_headers():
    provider = TheOddsApiProvider("k")
    headers = {"x-requests-last": "1", "x-requests-remaining": "499"}
    assert provider.request_cost(raw([], headers)) == 1
    assert provider.quota_remaining(raw([], headers)) == 499
    assert provider.request_cost(raw([])) == 1  # fallback: 1 region x 1 market


def test_request_shape_and_key_never_stored():
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=load(), headers={"x-requests-last": "1"})

    client = ProviderHttpClient(
        source="the_odds_api", base_url=BASE_URL, auth_params={"apiKey": "SECRET"},
        classify_body=classify_body, sleep=lambda s: None,
        transport=httpx.MockTransport(handler),
    )  # fmt: skip
    result = TheOddsApiProvider("SECRET", client=client).fetch_odds("PD")

    req = seen[0]
    assert req.url.path == "/v4/sports/soccer_spain_la_liga/odds"
    params = dict(req.url.params)
    assert params.pop("commenceTimeFrom").endswith("Z")  # pre-match only (QA F3 #1)
    assert params == {
        "regions": "eu", "markets": "h2h", "oddsFormat": "decimal", "dateFormat": "iso",
        "apiKey": "SECRET",
    }  # fmt: skip
    assert "SECRET" not in result.url
