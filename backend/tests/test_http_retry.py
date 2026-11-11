"""Transient venue failures must not reach the operator.

Jupiter returned HTTP 500 in production and the bot surfaced it immediately.
A single blip should be retried away; only a sustained outage should show.
"""

import httpx
import pytest

from app.connectors.base import RateLimited
from app.connectors.http import get_with_retry


class FakeClient:
    """Replays a scripted sequence of responses or exceptions."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def get(self, url, params=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self):
        return None


def response(status, body=None, headers=None):
    return httpx.Response(
        status, json=body if body is not None else {}, headers=headers or {},
        request=httpx.Request("GET", "https://venue.test/q"),
    )


async def test_a_single_500_is_retried_away():
    client = FakeClient([response(500), response(200, {"outAmount": "123"})])
    r = await get_with_retry(client, "/q", venue="Jupiter", attempts=3)
    assert r.status_code == 200
    assert r.json()["outAmount"] == "123"
    assert client.calls == 2, "should have retried exactly once"


async def test_two_failures_then_success():
    client = FakeClient([response(502), response(503), response(200)])
    r = await get_with_retry(client, "/q", venue="Jupiter", attempts=3)
    assert r.status_code == 200
    assert client.calls == 3


async def test_a_sustained_outage_still_raises():
    client = FakeClient([response(500), response(500), response(500)])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await get_with_retry(client, "/q", venue="Jupiter", attempts=3)
    assert client.calls == 3, "should have used every attempt"


async def test_timeouts_are_retried():
    client = FakeClient([
        httpx.ConnectTimeout("timed out"),
        response(200),
    ])
    r = await get_with_retry(client, "/q", venue="Jupiter", attempts=3)
    assert r.status_code == 200
    assert client.calls == 2


async def test_rate_limiting_is_not_retried_but_surfaced():
    """429 means slow down. Retrying immediately is the wrong response - the
    scan loop owns backing off."""
    client = FakeClient([response(429, headers={"Retry-After": "11"})])
    with pytest.raises(RateLimited) as exc:
        await get_with_retry(client, "/q", venue="Jupiter", attempts=3)
    assert exc.value.retry_after == 11.0
    assert client.calls == 1, "must not hammer a venue that asked us to stop"


async def test_client_errors_are_not_retried():
    """A 400 will not become a 200 by asking again."""
    client = FakeClient([response(400)])
    with pytest.raises(httpx.HTTPStatusError):
        await get_with_retry(client, "/q", venue="Jupiter", attempts=3)
    assert client.calls == 1


async def test_jupiter_quote_survives_a_transient_500(monkeypatch):
    """End to end through the connector, which is what the scan loop calls."""
    from app.connectors.jupiter import JupiterConnector

    conn = JupiterConnector()
    buy = {"outAmount": str(10**9)}      # 1 SOL out
    sell = {"outAmount": str(100 * 10**6)}  # 100 USDC back
    client = FakeClient([response(500), response(200, buy), response(200, sell)])
    monkeypatch.setattr(conn, "client", client)

    quote = await conn.get_quote("SOL/USDC")

    assert quote.bid > 0 and quote.ask > 0
    assert client.calls == 3, "one retry plus the two real quote calls"
    await conn.aclose()
