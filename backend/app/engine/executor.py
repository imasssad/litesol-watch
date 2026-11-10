import asyncio
from datetime import datetime, UTC
from app.config import settings
from app.models.schemas import Opportunity, Side, TradeRecord


class TradeExecutor:
    def __init__(self, cex, dex, risk):
        self.cex = cex
        self.dex = dex
        self.risk = risk

    async def execute(self, opportunity: Opportunity) -> TradeRecord:
        mode = "live" if settings.live_execution_allowed else "paper"
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

        try:
            buy_connector = self.cex if opportunity.buy_venue == self.cex.venue else self.dex
            sell_connector = self.cex if opportunity.sell_venue == self.cex.venue else self.dex

            # Requote immediately before execution to reduce stale-spread fills.
            buy_quote, sell_quote = await asyncio.gather(
                buy_connector.get_quote(opportunity.pair),
                sell_connector.get_quote(opportunity.pair),
            )
            fresh_opp = opportunity.model_copy(update={
                "buy_price": buy_quote.ask,
                "sell_price": sell_quote.bid,
            })
            ok, reason = self.risk.validate(fresh_opp, [buy_quote, sell_quote])
            if not ok:
                return self._failed(opportunity, mode, f"pre-trade validation failed: {reason}")

            # Prefunded inventory model: execute both legs concurrently.
            buy_task = buy_connector.market_order(opportunity.pair, Side.buy, opportunity.trade_size_usd)
            sell_task = sell_connector.market_order(opportunity.pair, Side.sell, opportunity.trade_size_usd)
            buy_res, sell_res = await asyncio.gather(buy_task, sell_task)
            realized = ((sell_res.avg_price - buy_res.avg_price) / buy_res.avg_price) * opportunity.trade_size_usd if buy_res.avg_price else None
            if realized is not None:
                self.risk.record_pnl(realized)
            return TradeRecord(
                ts=datetime.now(UTC),
                pair=opportunity.pair,
                direction=opportunity.direction,
                buy_venue=opportunity.buy_venue,
                sell_venue=opportunity.sell_venue,
                trade_size_usd=opportunity.trade_size_usd,
                expected_profit_usd=opportunity.expected_profit_usd,
                realized_profit_usd=realized,
                status="live_filled",
                mode=mode,
                buy_order_id=buy_res.order_id,
                sell_order_id=sell_res.order_id,
            )
        except Exception as exc:
            self.risk.record_failure()
            return self._failed(opportunity, mode, str(exc))

    def _failed(self, opportunity: Opportunity, mode: str, error: str) -> TradeRecord:
        self.risk.record_failure()
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
