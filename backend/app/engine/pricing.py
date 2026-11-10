"""Opportunity maths, shared by the scanner and the executor.

The executor must recompute the spread from fresh quotes before it fires, so the
calculation lives here rather than being inlined in the scanner.
"""

from datetime import UTC, datetime

from app.config import settings
from app.models.schemas import Opportunity, Quote

MIN_PRICE = 1e-12
# Sentinel for "never executable". Not -inf: float("-inf") is not valid JSON and
# would make the API emit `-Infinity`, which the dashboard cannot parse.
UNPRICEABLE_BPS = -1e9


def total_cost_bps(trade_size_usd: float) -> float:
    """Fees + slippage buffer + amortised gas, in basis points."""
    if trade_size_usd <= 0:
        raise ValueError("trade_size_usd must be greater than 0")
    fee_bps = settings.cex_fee_bps + settings.dex_fee_bps
    gas_bps = (settings.gas_buffer_usd / trade_size_usd) * 10000
    return fee_bps + settings.slippage_buffer_bps + gas_bps


def build_opportunity(buy: Quote, sell: Quote) -> Opportunity:
    """Price one direction: take `buy`'s ask, hit `sell`'s bid."""
    buy_price = buy.ask
    sell_price = sell.bid
    trade_size_usd = settings.effective_trade_size_usd

    if buy_price <= MIN_PRICE or sell_price <= MIN_PRICE:
        # A venue returning a zero/negative price is a data fault, not an edge.
        return Opportunity(
            ts=datetime.now(UTC),
            pair=settings.pair,
            direction=f"buy {buy.venue} / sell {sell.venue}",
            buy_venue=buy.venue,
            sell_venue=sell.venue,
            buy_price=buy_price,
            sell_price=sell_price,
            gross_spread_bps=0.0,
            estimated_cost_usd=0.0,
            net_spread_bps=UNPRICEABLE_BPS,
            trade_size_usd=trade_size_usd,
            expected_profit_usd=0.0,
            executable=False,
            reason=f"invalid price (buy={buy_price}, sell={sell_price})",
        )

    gross_spread_bps = ((sell_price - buy_price) / buy_price) * 10000
    cost_bps = total_cost_bps(trade_size_usd)
    net_spread_bps = gross_spread_bps - cost_bps
    expected_profit_usd = (net_spread_bps / 10000) * trade_size_usd

    return Opportunity(
        ts=datetime.now(UTC),
        pair=settings.pair,
        direction=f"buy {buy.venue} / sell {sell.venue}",
        buy_venue=buy.venue,
        sell_venue=sell.venue,
        buy_price=round(buy_price, 8),
        sell_price=round(sell_price, 8),
        gross_spread_bps=round(gross_spread_bps, 2),
        estimated_cost_usd=round((cost_bps / 10000) * trade_size_usd, 4),
        net_spread_bps=round(net_spread_bps, 2),
        trade_size_usd=trade_size_usd,
        expected_profit_usd=round(expected_profit_usd, 4),
        executable=False,
        reason="not checked",
    )


def reprice(opportunity: Opportunity, buy: Quote, sell: Quote) -> Opportunity:
    """Re-derive an opportunity from fresh quotes, keeping its direction.

    `Opportunity.model_copy(update={"buy_price": ...})` is NOT enough: it leaves
    gross/net spread and expected profit at their stale values, so a risk check
    on the copy would pass on an edge that no longer exists.
    """
    fresh = build_opportunity(buy=buy, sell=sell)
    return fresh.model_copy(update={"direction": opportunity.direction})
