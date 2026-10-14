import httpx
import pytest

from app.services.errors import AuthError, ProviderUnavailableError, ThrottledError
from app.services.http import ProviderHttpClient


def make_client(handler, sleeps: list[float], **kw) -> ProviderHttpClient:
    return ProviderHttpClient(
        source="test",
        base_url="https://provider.test",
        auth_headers={"X-Auth-Token": "secret-key"},
        sleep=sleeps.append,
        transport=httpx.MockTransport(handler),
        **kw,
    )


def test_retries_5xx_then_succeeds():
    calls = iter([httpx.Response(503), httpx.Response(200, json={"ok": True})])
    sleeps: list[float] = []
    raw = make_client(lambda req: next(calls), sleeps).get_json("/x", {"a": 1})

    assert raw.payload == {"ok": True}
    assert raw.attempts == 2
    assert sleeps == [2.0]
    assert "secret-key" not in raw.url  # key travels in a header, never in the URL


def test_429_honors_retry_after_and_gives_up():
    sleeps: list[float] = []
    client = make_client(lambda req: httpx.Response(429, headers={"Retry-After": "7"}), sleeps)

    with pytest.raises(ThrottledError) as exc:
        client.get_json("/x")
    assert exc.value.attempts == 3
    assert sleeps == [7.0, 7.0]  # no sleep after the last attempt


def test_401_is_not_retried():
    sleeps: list[float] = []
    client = make_client(lambda req: httpx.Response(401, text="bad key"), sleeps)

    with pytest.raises(AuthError):
        client.get_json("/x")
    assert sleeps == []


def test_network_error_exhausts_attempts():
    def boom(req):
        raise httpx.ConnectError("down")

    sleeps: list[float] = []
    with pytest.raises(ProviderUnavailableError):
        make_client(boom, sleeps, max_attempts=2).get_json("/x")
    assert sleeps == [2.0]
