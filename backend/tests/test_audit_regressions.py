"""Regression tests for each defect found in the audit.

Every test here fails against the code as it was uploaded.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.config import PLACEHOLDER_API_TOKEN, Settings
from app.engine.pricing import build_opportunity, reprice
from app.engine.risk import RiskEngine
from app.main import FRONTEND_DIR, require_auth

# --- 1. the dashboard path, which stopped the app from booting at all ---------


def test_frontend_dir_points_at_the_real_frontend():
    assert FRONTEND_DIR.name == "frontend"
    assert FRONTEND_DIR.is_dir(), f"{FRONTEND_DIR} does not exist"
    assert (FRONTEND_DIR / "index.html").is_file()


# --- 2. control-endpoint auth must fail closed --------------------------------


def test_auth_rejects_missing_token():
    with pytest.raises(HTTPException) as exc:
        require_auth(x_api_token=None)
    assert exc.value.status_code == 401


def test_auth_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc:
        require_auth(x_api_token="not-the-token")
    assert exc.value.status_code == 401


def test_auth_accepts_configured_token():
    from app.config import settings

    require_auth(x_api_token=settings.api_auth_token)


def test_placeholder_token_disables_control_endpoints(monkeypatch):
    """The placeholder used to switch auth OFF instead of locking the API."""
    from app.config import settings

    monkeypatch.setattr(settings, "api_auth_token", PLACEHOLDER_API_TOKEN)
    assert settings.auth_configured is False
    with pytest.raises(HTTPException) as exc:
        require_auth(x_api_token=None)
    assert exc.value.status_code == 503


def test_token_comparison_rejects_non_string():
    from app.config import settings

    assert settings.verify_api_token(None) is False
    assert settings.verify_api_token(12345) is False


# --- 3. the requote must recompute the spread --------------------------------


def test_reprice_recomputes_spread_when_the_edge_disappears(quote):
    good = build_opportunity(
        buy=quote("CEX", bid=99.0, ask=100.0),
        sell=quote("DEX", bid=102.0, ask=103.0),
    )
    assert good.net_spread_bps > 0

    # Prices invert between the scan and the fill.
    fresh = reprice(
        good,
        buy=quote("CEX", bid=101.0, ask=102.0),
        sell=quote("DEX", bid=100.0, ask=101.0),
    )
    assert fresh.net_spread_bps < 0, "stale spread was carried forward"
    assert fresh.gross_spread_bps < 0
    assert fresh.expected_profit_usd < 0

    ok, reason = RiskEngine().validate(fresh, [])
    assert ok is False
    assert "net spread" in reason


def test_model_copy_alone_would_have_passed_validation(quote):
    """Pins the exact shape of the original bug."""
    good = build_opportunity(
        buy=quote("CEX", bid=99.0, ask=100.0),
        sell=quote("DEX", bid=112.0, ask=113.0),
    )
    naive = good.model_copy(update={"buy_price": 102.0, "sell_price": 100.0})
    assert naive.net_spread_bps == good.net_spread_bps  # the latent bug
    assert RiskEngine().validate(naive, [])[0] is True  # ...would have traded

    correct = reprice(
        good,
        buy=quote("CEX", bid=101.0, ask=102.0),
        sell=quote("DEX", bid=100.0, ask=101.0),
    )
    assert RiskEngine().validate(correct, [])[0] is False


# --- 4. pricing guards -------------------------------------------------------


def test_zero_price_does_not_raise(quote):
    opp = build_opportunity(
        buy=quote("CEX", bid=0.0, ask=0.0),
        sell=quote("DEX", bid=100.0, ask=101.0),
    )
    assert opp.executable is False
    assert "invalid price" in opp.reason


def test_unpriceable_opportunity_is_json_safe(quote):
    """net_spread_bps must stay finite: -inf serialises as invalid JSON."""
    import json

    opp = build_opportunity(
        buy=quote("CEX", bid=0.0, ask=0.0),
        sell=quote("DEX", bid=100.0, ask=101.0),
    )
    encoded = json.dumps(opp.model_dump(mode="json"), allow_nan=False)
    assert "Infinity" not in encoded


def test_negative_price_is_rejected_by_risk(quote):
    opp = build_opportunity(
        buy=quote("CEX", bid=99.0, ask=100.0),
        sell=quote("DEX", bid=102.0, ask=103.0),
    )
    broken = opp.model_copy(update={"buy_price": 0.0})
    ok, reason = RiskEngine().validate(broken, [])
    assert ok is False
    assert reason == "invalid venue price"


# --- 5. consecutive-failure counting ----------------------------------------


def test_consecutive_failures_reset_on_success():
    risk = RiskEngine()
    risk.record_failure("a")
    risk.record_failure("b")
    assert risk.kill_switch is False
    risk.record_success()
    assert risk.failed_trade_count == 0

    # Two more failures must not trip a limit of 3 now that the run was broken.
    risk.record_failure("c")
    risk.record_failure("d")
    assert risk.kill_switch is False
    risk.record_failure("e")
    assert risk.kill_switch is True


def test_kill_switch_records_a_reason():
    risk = RiskEngine()
    for i in range(3):
        risk.record_failure(f"boom {i}")
    assert risk.kill_switch is True
    assert risk.kill_switch_reason is not None
    assert "consecutive failures" in risk.kill_switch_reason


def test_daily_loss_limit_trips_kill_switch():
    risk = RiskEngine()
    risk.record_pnl(-1000.0)
    assert risk.kill_switch is True
    assert "daily loss" in risk.kill_switch_reason


# --- 6. quote freshness ------------------------------------------------------


def test_stale_quote_is_rejected(quote):
    opp = build_opportunity(
        buy=quote("CEX", bid=99.0, ask=100.0),
        sell=quote("DEX", bid=112.0, ask=113.0),
    )
    old = quote("CEX", bid=99.0, ask=100.0, ts=datetime.now(UTC) - timedelta(seconds=60))
    ok, reason = RiskEngine().validate(opp, [old])
    assert ok is False
    assert "stale quote" in reason


def test_future_dated_quote_is_rejected(quote):
    opp = build_opportunity(
        buy=quote("CEX", bid=99.0, ask=100.0),
        sell=quote("DEX", bid=112.0, ask=113.0),
    )
    ahead = quote("CEX", bid=99.0, ask=100.0, ts=datetime.now(UTC) + timedelta(seconds=30))
    ok, reason = RiskEngine().validate(opp, [ahead])
    assert ok is False
    assert "future" in reason


# --- 7. config validation ----------------------------------------------------


def _settings(**overrides):
    base = {
        "api_auth_token": "a-long-random-test-token",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


def test_pair_must_match_base_and_quote_assets():
    with pytest.raises(ValueError, match="does not match"):
        _settings(pair="SOL/USDT", base_asset="SOL", quote_asset="USDC")


def test_matching_pair_is_accepted():
    s = _settings(pair="SOL/USDT", base_asset="SOL", quote_asset="USDT")
    assert s.symbol_binance == "SOLUSDT"
    assert s.symbol_ccxt == "SOL/USDT"


def test_unsupported_cex_venue_is_rejected_at_config_time():
    with pytest.raises(ValueError, match="not supported"):
        _settings(cex_venue="kraken")


def test_live_execution_requires_a_real_api_token():
    with pytest.raises(ValueError, match="placeholder"):
        _settings(
            api_auth_token=PLACEHOLDER_API_TOKEN,
            live_trading=True,
            execution_enabled=True,
        )


def test_live_execution_needs_both_gates():
    assert _settings(live_trading=True, execution_enabled=False).live_execution_allowed is False
    assert _settings(live_trading=False, execution_enabled=True).live_execution_allowed is False


def test_trade_size_is_capped_by_max_trade_usd():
    s = _settings(trade_size_usd=5000, max_trade_usd=250)
    assert s.effective_trade_size_usd == 250


def test_non_positive_sizes_are_rejected():
    with pytest.raises(ValueError, match="greater than 0"):
        _settings(trade_size_usd=0)
    with pytest.raises(ValueError, match="greater than 0"):
        _settings(scan_interval_seconds=0)


# --- 8. Jupiter asset resolution --------------------------------------------


def test_unknown_asset_gives_a_clear_error_not_a_keyerror():
    from app.connectors.jupiter import JupiterConnector

    with pytest.raises(RuntimeError, match="no known Solana mint"):
        JupiterConnector._asset("WIF")


def test_known_mints_are_32_byte_base58_pubkeys():
    from app.connectors.jupiter import DECIMALS, MINTS

    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    assert set(MINTS) == set(DECIMALS)
    for symbol, mint in MINTS.items():
        n = 0
        for ch in mint:
            assert ch in alphabet, f"{symbol}: {ch!r} is not base58"
            n = n * 58 + alphabet.index(ch)
        length = (n.bit_length() + 7) // 8 + (len(mint) - len(mint.lstrip("1")))
        assert length == 32, f"{symbol} mint is not a 32-byte pubkey"


# --- 9. Binance quantity rounding -------------------------------------------


@pytest.mark.parametrize(
    "qty,step,expected",
    [
        (1.23456789, 0.001, "1.234"),
        (1.0, 0.01, "1.00"),
        (0.4999, 0.5, "0.0"),
        (12.9, 1.0, "12"),
    ],
)
def test_sell_quantity_rounds_down_to_the_lot_step(qty, step, expected):
    from app.connectors.binance_spot import BinanceSpotConnector

    _, as_string = BinanceSpotConnector._round_step(qty, step)
    assert as_string == expected


# --- 10. db timestamps round-trip with a timezone ---------------------------


def test_db_timestamp_columns_are_timezone_aware():
    from app.db.models import OpportunityRow, TradeRow

    for model in (OpportunityRow, TradeRow):
        assert model.__table__.c.ts.type.timezone is True, model.__name__


def test_naive_db_timestamps_are_normalised_to_utc():
    """SQLite drops the offset, so the repository must re-attach UTC on read."""
    from app.db.models import OpportunityRow
    from app.services.repository import _row_to_dict

    class Row:
        pass

    row = Row()
    for column in OpportunityRow.__table__.columns:
        setattr(row, column.name, None)
    row.ts = datetime(2026, 1, 1, 12, 0, 0)  # naive, as SQLite returns it

    data = _row_to_dict(row, OpportunityRow.__table__)
    assert data["ts"].tzinfo is not None
    assert data["ts"].utcoffset().total_seconds() == 0
    assert "id" not in data


def test_aware_db_timestamps_are_left_alone():
    from app.db.models import TradeRow
    from app.services.repository import _row_to_dict

    class Row:
        pass

    row = Row()
    for column in TradeRow.__table__.columns:
        setattr(row, column.name, None)
    row.ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    assert _row_to_dict(row, TradeRow.__table__)["ts"] == row.ts


# --- 11. the venue factory --------------------------------------------------


def test_factory_rejects_an_unknown_venue(monkeypatch):
    from app.config import settings
    from app.connectors.factory import build_cex_connector

    monkeypatch.setattr(settings, "cex_venue", "ftx")
    with pytest.raises(ValueError, match="Unsupported CEX_VENUE"):
        build_cex_connector()


# --- 12. the mock connectors are reachable, not dead code -------------------


def test_mock_venues_are_selectable(monkeypatch):
    from app.config import settings
    from app.connectors.factory import build_cex_connector, build_dex_connector

    monkeypatch.setattr(settings, "cex_venue", "mock")
    monkeypatch.setattr(settings, "dex_venue", "mock")
    assert build_cex_connector().venue == "Mock CEX"
    assert build_dex_connector().venue == "Mock DEX"


async def test_mock_connectors_refuse_to_place_orders():
    from app.connectors.mock_cex import MockCexConnector
    from app.models.schemas import Side

    with pytest.raises(RuntimeError, match="cannot place orders"):
        await MockCexConnector().market_order("SOL/USDC", Side.buy, 100.0)


async def test_mock_connector_quotes_are_well_formed():
    from app.connectors.mock_dex import MockDexConnector

    q = await MockDexConnector().get_quote("SOL/USDC")
    assert q.bid > 0 and q.ask > q.bid
    assert q.ts.tzinfo is not None


def test_unsupported_dex_venue_is_rejected_at_config_time():
    with pytest.raises(ValueError, match="DEX_VENUE"):
        _settings(dex_venue="uniswap")
