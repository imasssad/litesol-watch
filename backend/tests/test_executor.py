"""Executor behaviour, in particular what happens when one leg fails."""

from datetime import datetime, UTC

import pytest

from app.config import settings
from app.engine.executor import TradeExecutor
from app.engine.pricing import build_opportunity
from app.engine.risk import RiskEngine
from app.models.schemas import OrderResult, Quote, Side


class FakeConnector:
    def __init__(self, venue, bid, ask, order=None, order_error=None):
        self.venue = venue
        self.bid = bid
        self.ask = ask
        self._order = order
        self._order_error = order_error
        self.orders = []

    async def get_quote(self, pair):
        return Quote(
            venue=self.venue, pair=pair, bid=self.bid, ask=self.ask, ts=datetime.now(UTC)
        )

    async def market_order(self, pair, side, quote_size_usd):
        self.orders.append((side, quote_size_usd))
        if self._order_error is not None:
            raise self._order_error
        return self._order or OrderResult(
            venue=self.venue,
            order_id=f"{self.venue}-1",
            status="FILLED",
            side=side,
            pair=pair,
            filled_qty=1.0,
            avg_price=self.ask if side == Side.buy else self.bid,
        )


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, "live_trading", True)
    monkeypatch.setattr(settings, "execution_enabled", True)


def _opportunity(cex, dex):
    opp = build_opportunity(
        buy=Quote(venue=cex.venue, pair="SOL/USDC", bid=cex.bid, ask=cex.ask, ts=datetime.now(UTC)),
        sell=Quote(venue=dex.venue, pair="SOL/USDC", bid=dex.bid, ask=dex.ask, ts=datetime.now(UTC)),
    )
    return opp


async def test_paper_mode_never_calls_the_venues():
    cex = FakeConnector("CEX", 99.0, 100.0)
    dex = FakeConnector("DEX", 130.0, 131.0)
    executor = TradeExecutor(cex, dex, RiskEngine())

    record = await executor.execute(_opportunity(cex, dex))

    assert record.mode == "paper"
    assert record.status == "paper_filled"
    assert cex.orders == [] and dex.orders == []


async def test_requote_blocks_a_trade_whose_edge_has_gone(live):
    cex = FakeConnector("CEX", 99.0, 100.0)
    dex = FakeConnector("DEX", 130.0, 131.0)
    executor = TradeExecutor(cex, dex, RiskEngine())
    opp = _opportunity(cex, dex)

    # The edge evaporates before execution.
    dex.bid, dex.ask = 99.5, 100.5

    record = await executor.execute(opp)

    assert record.status == "failed"
    assert "pre-trade validation failed" in record.error
    assert cex.orders == [] and dex.orders == [], "orders were sent on a dead edge"


async def test_one_sided_fill_is_flagged_and_trips_the_kill_switch(live):
    cex = FakeConnector("CEX", 99.0, 100.0)
    dex = FakeConnector("DEX", 130.0, 131.0, order_error=RuntimeError("venue 500"))
    risk = RiskEngine()
    executor = TradeExecutor(cex, dex, risk)

    record = await executor.execute(_opportunity(cex, dex))

    assert record.status == "one_leg_filled"
    assert "UNHEDGED POSITION" in record.error
    # The leg that did fill must still be identifiable for reconciliation.
    assert record.buy_order_id == "CEX-1"
    assert record.sell_order_id is None
    assert risk.kill_switch is True
    assert "one-sided fill" in risk.kill_switch_reason


async def test_both_legs_failing_is_a_plain_failure(live):
    cex = FakeConnector("CEX", 99.0, 100.0, order_error=RuntimeError("cex down"))
    dex = FakeConnector("DEX", 130.0, 131.0, order_error=RuntimeError("dex down"))
    risk = RiskEngine()
    executor = TradeExecutor(cex, dex, risk)

    record = await executor.execute(_opportunity(cex, dex))

    assert record.status == "failed"
    assert "both legs failed" in record.error
    assert risk.kill_switch is False  # only the failure counter moved
    assert risk.failed_trade_count == 1


async def test_successful_trade_clears_the_failure_counter(live):
    cex = FakeConnector("CEX", 99.0, 100.0)
    dex = FakeConnector("DEX", 130.0, 131.0)
    risk = RiskEngine()
    risk.record_failure("earlier blip")
    executor = TradeExecutor(cex, dex, risk)

    record = await executor.execute(_opportunity(cex, dex))

    assert record.status == "live_filled"
    assert risk.failed_trade_count == 0
    assert record.realized_profit_usd is not None


async def test_realized_pnl_is_net_of_fees(live):
    cex = FakeConnector("CEX", 99.0, 100.0)
    dex = FakeConnector("DEX", 130.0, 131.0)
    executor = TradeExecutor(cex, dex, RiskEngine())

    record = await executor.execute(_opportunity(cex, dex))

    gross = 130.0 - 100.0  # qty is 1.0 on both legs
    assert record.realized_profit_usd < gross, "fees were not deducted"


async def test_unknown_venue_is_reported_not_silently_routed(live):
    cex = FakeConnector("CEX", 99.0, 100.0)
    dex = FakeConnector("DEX", 130.0, 131.0)
    executor = TradeExecutor(cex, dex, RiskEngine())
    opp = _opportunity(cex, dex).model_copy(update={"buy_venue": "Mystery Venue"})

    record = await executor.execute(opp)

    assert record.status == "failed"
    assert "no connector for venue" in record.error
