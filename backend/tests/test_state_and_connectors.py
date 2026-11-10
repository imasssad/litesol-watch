"""Phase-2 follow-up findings: dead Jupiter endpoint, state lost on restart,
Binance clock skew. Each test fails against the previous commit."""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings

# --- A. Jupiter: quote-api.jup.ag/v6 was retired on 2025-10-01 ----------------


def test_default_jupiter_urls_are_not_the_retired_v6_host():
    assert "quote-api.jup.ag" not in settings.jupiter_quote_url
    assert "quote-api.jup.ag" not in settings.jupiter_swap_url
    assert "/swap/v1/" in settings.jupiter_quote_url


def test_jupiter_api_key_is_sent_as_x_api_key(monkeypatch):
    from app.connectors.jupiter import JupiterConnector

    monkeypatch.setattr(settings, "jupiter_api_key", "test-key-123")
    conn = JupiterConnector()
    assert conn.client.headers.get("x-api-key") == "test-key-123"


def test_no_api_key_header_when_unset(monkeypatch):
    from app.connectors.jupiter import JupiterConnector

    monkeypatch.setattr(settings, "jupiter_api_key", "")
    conn = JupiterConnector()
    assert "x-api-key" not in conn.client.headers


# --- B. Risk state must survive a restart ------------------------------------


async def test_bot_state_round_trips_through_the_db():
    from app.db.init_db import init_db
    from app.services.repository import load_bot_state, save_bot_state

    await init_db()
    await save_bot_state(kill_switch=True, reason="one-sided fill")
    state = await load_bot_state()
    assert state is not None
    assert state.kill_switch is True
    assert state.kill_switch_reason == "one-sided fill"

    await save_bot_state(kill_switch=False, reason=None)
    state = await load_bot_state()
    assert state.kill_switch is False
    assert state.kill_switch_reason is None


async def test_todays_live_pnl_is_recomputed_from_trades():
    from app.db.init_db import init_db
    from app.models.schemas import TradeRecord
    from app.services.repository import realized_pnl_since, save_trade

    await init_db()
    now = datetime.now(UTC)

    def trade(ts, mode, pnl):
        return TradeRecord(
            ts=ts,
            pair="SOL/USDC",
            direction="d",
            buy_venue="A",
            sell_venue="B",
            trade_size_usd=100,
            expected_profit_usd=0,
            realized_profit_usd=pnl,
            status="live_filled" if mode == "live" else "paper_filled",
            mode=mode,
        )

    await save_trade(trade(now, "live", -12.5))
    await save_trade(trade(now, "live", 2.5))
    await save_trade(trade(now, "paper", -999.0))  # paper must not count
    await save_trade(trade(now - timedelta(days=2), "live", -500.0))  # not today

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    pnl = await realized_pnl_since(start_of_day)
    assert pnl == pytest.approx(-10.0)


async def test_restore_reengages_kill_switch_after_restart(monkeypatch):
    """A restart used to silently clear the kill switch and the daily loss."""
    from app.engine.risk import RiskEngine

    monkeypatch.setattr(settings, "max_daily_loss_usd", 100.0)

    # Simulated restart: fresh engine, state loaded from persistence.
    risk = RiskEngine()
    risk.restore(kill_switch=True, reason="persisted: manual via API", daily_pnl_usd=-5.0)
    assert risk.kill_switch is True
    assert "persisted" in risk.kill_switch_reason

    # Even with no persisted switch, a recomputed daily loss past the limit
    # must re-engage it rather than let the bot resume trading.
    risk2 = RiskEngine()
    risk2.restore(kill_switch=False, reason=None, daily_pnl_usd=-150.0)
    assert risk2.kill_switch is True
    assert "daily loss" in risk2.kill_switch_reason


async def test_scanner_persists_kill_switch_changes():
    from app.db.init_db import init_db
    from app.engine.scanner import ArbScanner
    from app.services.repository import load_bot_state

    await init_db()
    scanner = ArbScanner()
    scanner.risk.enable_kill_switch("test reason")
    await scanner.sync_risk_state()

    state = await load_bot_state()
    assert state.kill_switch is True
    assert state.kill_switch_reason == "test reason"


# --- C. Binance signing must tolerate local clock skew -----------------------


async def test_binance_timestamp_uses_server_time_offset(monkeypatch):
    from app.connectors.binance_spot import BinanceSpotConnector

    conn = BinanceSpotConnector()

    # Local clock is 7 seconds behind the exchange - more than recvWindow.
    skew_ms = 7000

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            import time

            return {"serverTime": int(time.time() * 1000) + skew_ms}

    async def fake_get(url, **kwargs):
        assert url == "/api/v3/time"
        return FakeResponse()

    monkeypatch.setattr(conn.client, "get", fake_get)

    import time

    ts = await conn._timestamp_ms()
    assert abs(ts - (int(time.time() * 1000) + skew_ms)) < 1500, (
        "order timestamp does not track Binance server time; "
        "a skewed clock means every order is rejected with -1021"
    )
    await conn.aclose()


async def test_binance_timestamp_falls_back_to_local_clock(monkeypatch):
    from app.connectors.binance_spot import BinanceSpotConnector

    conn = BinanceSpotConnector()

    async def failing_get(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(conn.client, "get", failing_get)

    import time

    ts = await conn._timestamp_ms()
    assert abs(ts - int(time.time() * 1000)) < 1500
    await conn.aclose()
