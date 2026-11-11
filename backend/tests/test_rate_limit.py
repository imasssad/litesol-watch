"""Rate limiting must not be treated as a trading failure.

Production surfaced this: Jupiter's keyless tier returned HTTP 429, the
scanner counted each one as a failure, and the kill switch tripped - which
needs a manual reset. Being throttled means we are blind, not broken.
"""

import asyncio

import pytest

from app.config import settings
from app.connectors.base import RateLimited
from app.engine.scanner import MAX_BACKOFF_SECONDS, ArbScanner


def test_rate_limited_carries_venue_and_retry_after():
    exc = RateLimited("Jupiter", retry_after=12.0)
    assert exc.venue == "Jupiter"
    assert exc.retry_after == 12.0
    assert "Jupiter" in str(exc)


def test_rate_limited_is_not_a_generic_runtime_error_by_accident():
    # It subclasses RuntimeError for convenience, but the scanner must branch
    # on the specific type before the generic handler.
    assert issubclass(RateLimited, RuntimeError)


async def test_throttling_does_not_count_as_a_failure(monkeypatch):
    from app.db.init_db import init_db

    await init_db()
    monkeypatch.setattr(settings, "scan_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "max_consecutive_failures", 2)

    scanner = ArbScanner()
    attempts = []

    async def throttled():
        attempts.append(1)
        raise RateLimited("Jupiter", retry_after=0.01)

    monkeypatch.setattr(scanner, "scan_once", throttled)

    scanner.running = True
    task = asyncio.create_task(scanner._loop())
    await asyncio.sleep(0.2)
    scanner.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(attempts) >= 2, "scanner should keep retrying while throttled"
    assert scanner.risk.failed_trade_count == 0, "429s burned the failure budget"
    assert scanner.risk.kill_switch is False, "throttling must not trip the kill switch"
    assert scanner.rate_limited is True


async def test_a_scan_error_does_not_trip_the_kill_switch(monkeypatch):
    """Failing to READ prices is not a trading failure.

    Jupiter returned HTTP 500 in production - its outage - and three of those
    halted the bot behind a kill switch a human had to reset. Being unable to
    scan carries no exposure: no order is attempted. Execution failures still
    count; see test_executor.py.
    """
    from app.db.init_db import init_db

    await init_db()
    monkeypatch.setattr(settings, "scan_interval_seconds", 0.01)
    monkeypatch.setattr(settings, "max_consecutive_failures", 2)

    scanner = ArbScanner()
    attempts = []

    async def venue_500():
        attempts.append(1)
        raise RuntimeError("Server error '500 Internal Server Error' from the venue")

    monkeypatch.setattr(scanner, "scan_once", venue_500)

    scanner.running = True
    task = asyncio.create_task(scanner._loop())
    await asyncio.sleep(0.2)
    scanner.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(attempts) >= 2, "scanner should keep retrying through a venue outage"
    assert scanner.risk.kill_switch is False, "a venue outage must not halt the bot"
    assert scanner.risk.failed_trade_count == 0
    assert scanner.last_scan_error is not None
    assert "500" in scanner.last_scan_error


async def test_execution_failures_still_trip_the_kill_switch():
    """The counterpart: money at risk still halts the bot."""
    from app.engine.risk import RiskEngine

    risk = RiskEngine()
    for _ in range(settings.max_consecutive_failures):
        risk.record_failure("order rejected by venue")
    assert risk.kill_switch is True


async def test_a_good_scan_clears_the_recorded_error(monkeypatch):
    from app.db.init_db import init_db

    await init_db()
    monkeypatch.setattr(settings, "scan_interval_seconds", 0.01)

    scanner = ArbScanner()
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("venue hiccup")
        return None

    monkeypatch.setattr(scanner, "scan_once", flaky)
    scanner.running = True
    task = asyncio.create_task(scanner._loop())
    await asyncio.sleep(0.3)
    scanner.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert scanner.last_scan_error is None, "a successful scan should clear it"


async def test_backoff_grows_and_is_capped(monkeypatch):
    from app.db.init_db import init_db

    await init_db()
    monkeypatch.setattr(settings, "scan_interval_seconds", 100.0)

    scanner = ArbScanner()
    slept = []

    async def throttled():
        raise RateLimited("Jupiter")  # no Retry-After -> exponential backoff

    async def fake_sleep(d):
        slept.append(d)
        if len(slept) >= 8:
            scanner.running = False

    monkeypatch.setattr(scanner, "scan_once", throttled)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    scanner.running = True
    await scanner._loop()

    assert slept[0] < slept[1], "backoff should grow while still throttled"
    assert max(slept) <= MAX_BACKOFF_SECONDS, "backoff must be capped"


async def test_backoff_clears_after_a_good_scan(monkeypatch):
    from app.db.init_db import init_db

    await init_db()
    monkeypatch.setattr(settings, "scan_interval_seconds", 0.01)

    scanner = ArbScanner()
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimited("Jupiter", retry_after=0.01)
        return None

    monkeypatch.setattr(scanner, "scan_once", flaky)
    scanner.running = True
    task = asyncio.create_task(scanner._loop())
    await asyncio.sleep(0.2)
    scanner.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert scanner.rate_limited is False, "backoff should reset once quotes flow again"


@pytest.mark.parametrize("status_code", [429, 418])
async def test_binance_maps_throttling_responses(status_code, monkeypatch):
    from app.connectors.binance_spot import BinanceSpotConnector

    conn = BinanceSpotConnector()

    class Resp:
        status_code = None
        headers = {"Retry-After": "7"}

    Resp.status_code = status_code

    async def fake_get(url, **kwargs):
        return Resp()

    monkeypatch.setattr(conn.client, "get", fake_get)
    with pytest.raises(RateLimited) as exc:
        await conn.get_quote("SOL/USDC")
    assert exc.value.retry_after == 7.0
    await conn.aclose()


async def test_jupiter_maps_429_to_rate_limited(monkeypatch):
    from app.connectors.jupiter import JupiterConnector

    conn = JupiterConnector()

    class Resp:
        status_code = 429
        headers = {"Retry-After": "3"}

    async def fake_get(url, **kwargs):
        return Resp()

    monkeypatch.setattr(conn.client, "get", fake_get)
    with pytest.raises(RateLimited) as exc:
        await conn.get_quote("SOL/USDC")
    assert exc.value.venue == "Jupiter"
    assert exc.value.retry_after == 3.0
    await conn.aclose()


# --- the manual scan endpoint must not leak bare 500s ----------------------


async def test_manual_scan_maps_rate_limiting_to_429(monkeypatch):
    """A 429 from a venue used to surface as "500 (Internal Server Error)"."""
    from fastapi import HTTPException

    from app.engine.scanner import scanner
    from app.main import scan_once

    async def throttled():
        raise RateLimited("Jupiter", retry_after=17.0)

    monkeypatch.setattr(scanner, "scan_once", throttled)
    before = scanner.risk.failed_trade_count

    with pytest.raises(HTTPException) as exc:
        await scan_once()

    assert exc.value.status_code == 429
    assert "Jupiter" in exc.value.detail
    assert exc.value.headers.get("Retry-After") == "17"
    assert scanner.risk.failed_trade_count == before, "throttling is not a failure"


async def test_manual_scan_maps_venue_errors_to_502(monkeypatch):
    from fastapi import HTTPException

    from app.db.init_db import init_db
    from app.engine.scanner import scanner
    from app.main import scan_once

    await init_db()

    async def broken():
        raise RuntimeError("venue returned garbage")

    monkeypatch.setattr(scanner, "scan_once", broken)
    scanner.risk.failed_trade_count = 0
    scanner.risk.kill_switch = False

    with pytest.raises(HTTPException) as exc:
        await scan_once()

    assert exc.value.status_code == 502
    assert "venue returned garbage" in exc.value.detail
    assert "RuntimeError" in exc.value.detail
    assert scanner.risk.failed_trade_count == 1, "a real error must still count"
