import asyncio
import logging
from datetime import datetime, UTC

from app.config import settings
from app.engine.pricing import reprice
from app.models.schemas import Opportunity, OrderResult, Side, TradeRecord

logger = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(self, cex, dex, risk):
        self.cex = cex
        self.dex = dex
        self.risk = risk

    def _connector_for(self, venue: str):
        if venue == self.cex.venue:
            return self.cex
        if venue == self.dex.venue:
            return self.dex
        raise RuntimeError(f"no connector for venue {venue!r}")

    async def execute(self, opportunity: Opportunity) -> TradeRecord:
        if not settings.live_execution_allowed:
            return TradeRecord(
                ts=datetime.now(UTC),
                pair=opportunity.pair,
                direction=opportunity.direction,
                buy_venue=opportunity.buy_venue,
                sell_venue=opportunity.sell_venue,
                trade_size_usd=opportunity.trade_size_usd,
                expected_profit_usd=opportunity.expected_profit_usd,
                status="paper_filled",
                mode="paper",
            )

        mode = "live"
        try:
            buy_connector = self._connector_for(opportunity.buy_venue)
            sell_connector = self._connector_for(opportunity.sell_venue)

            # Requote immediately before execution to reduce stale-spread fills.
            buy_quote, sell_quote = await asyncio.gather(
                buy_connector.get_quote(opportunity.pair),
                sell_connector.get_quote(opportunity.pair),
            )
            # Recompute the whole opportunity: carrying the old net_spread_bps
            # forward would make this check pass on an edge that has gone.
            fresh_opp = reprice(opportunity, buy=buy_quote, sell=sell_quote)
            ok, reason = self.risk.validate(fresh_opp, [buy_quote, sell_quote])
            if not ok:
                logger.info("pre-trade validation blocked the trade: %s", reason)
                return self._failed(
                    opportunity, mode, f"pre-trade validation failed: {reason}"
                )
        except Exception as exc:
            logger.exception("requote failed")
            return self._failed(opportunity, mode, f"requote failed: {exc}")

        size = opportunity.trade_size_usd
        # Prefunded inventory model: execute both legs concurrently. return_exceptions
        # keeps a throwing leg from cancelling the sibling task mid-flight and losing
        # the record of a leg that may already have filled.
        buy_res, sell_res = await asyncio.gather(
            buy_connector.market_order(opportunity.pair, Side.buy, size),
            sell_connector.market_order(opportunity.pair, Side.sell, size),
            return_exceptions=True,
        )

        buy_failed = isinstance(buy_res, BaseException)
        sell_failed = isinstance(sell_res, BaseException)

        if buy_failed and sell_failed:
            error = f"both legs failed: buy={buy_res}; sell={sell_res}"
            logger.error(error)
            return self._failed(opportunity, mode, error)

        if buy_failed or sell_failed:
            # One leg is on the book and the other is not: the bot is now holding
            # an unhedged position. Stop trading and surface it loudly instead of
            # logging a generic failure.
            filled = sell_res if buy_failed else buy_res
            failed_side = "buy" if buy_failed else "sell"
            error = (
                f"UNHEDGED POSITION: {failed_side} leg failed after the other leg "
                f"filled. filled_leg={filled.side.value} venue={filled.venue} "
                f"qty={filled.filled_qty} avg_price={filled.avg_price} "
                f"order_id={filled.order_id}. error={buy_res if buy_failed else sell_res}"
            )
            logger.critical(error)
            self.risk.enable_kill_switch("one-sided fill needs manual reconciliation")
            record = self._failed(opportunity, mode, error)
            return record.model_copy(
                update={
                    "status": "one_leg_filled",
                    "buy_order_id": None if buy_failed else filled.order_id,
                    "sell_order_id": None if sell_failed else filled.order_id,
                }
            )

        return self._filled(opportunity, mode, buy_res, sell_res)

    def _filled(
        self,
        opportunity: Opportunity,
        mode: str,
        buy_res: OrderResult,
        sell_res: OrderResult,
    ) -> TradeRecord:
        realized = None
        if buy_res.avg_price > 0 and sell_res.avg_price > 0:
            qty = min(buy_res.filled_qty, sell_res.filled_qty)
            gross = (sell_res.avg_price - buy_res.avg_price) * qty
            # Net the known fee estimate off the gross so the figure is not read
            # as a fee-free profit.
            notional = buy_res.avg_price * qty
            fees = notional * ((settings.cex_fee_bps + settings.dex_fee_bps) / 10000)
            realized = gross - fees - settings.gas_buffer_usd
            self.risk.record_pnl(realized)

        self.risk.record_success()
        return TradeRecord(
            ts=datetime.now(UTC),
            pair=opportunity.pair,
            direction=opportunity.direction,
            buy_venue=opportunity.buy_venue,
            sell_venue=opportunity.sell_venue,
            trade_size_usd=opportunity.trade_size_usd,
            expected_profit_usd=opportunity.expected_profit_usd,
            realized_profit_usd=None if realized is None else round(realized, 6),
            status="live_filled",
            mode=mode,
            buy_order_id=buy_res.order_id,
            sell_order_id=sell_res.order_id,
        )

    def _failed(self, opportunity: Opportunity, mode: str, error: str) -> TradeRecord:
        self.risk.record_failure(error)
        return TradeRecord(
            ts=datetime.now(UTC),
            pair=opportunity.pair,
            direction=opportunity.direction,
            buy_venue=opportunity.buy_venue,
            sell_venue=opportunity.sell_venue,
            trade_size_usd=opportunity.trade_size_usd,
            expected_profit_usd=opportunity.expected_profit_usd,
            status="failed",
            mode=mode,
            error=error,
        )
