import asyncio
from datetime import datetime, UTC
from app.config import settings
from app.connectors.factory import build_cex_connector
from app.connectors.jupiter import JupiterConnector
from app.engine.executor import TradeExecutor
from app.engine.risk import RiskEngine
from app.models.schemas import Opportunity, Quote, TradeRecord
from app.services.repository import save_opportunity, save_trade


class ArbScanner:
    def __init__(self):
        self.cex = build_cex_connector()
        self.dex = JupiterConnector()
        self.risk = RiskEngine()
        self.executor = TradeExecutor(self.cex, self.dex, self.risk)
        self.running = False
        self.latest_quotes: list[Quote] = []
        self.opportunities: list[Opportunity] = []
        self.trades: list[TradeRecord] = []
        self._task: asyncio.Task | None = None

    async def start(self):
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self):
        while self.running:
            try:
                await self.scan_once()
            except Exception:
                self.risk.record_failure()
            await asyncio.sleep(settings.scan_interval_seconds)

    async def health(self) -> dict:
        cex, dex = await asyncio.gather(self.cex.health(), self.dex.health())
        return {"cex": cex, "dex": dex}

    async def scan_once(self) -> Opportunity:
        cex_quote, dex_quote = await asyncio.gather(
            self.cex.get_quote(settings.pair),
            self.dex.get_quote(settings.pair),
        )
        self.latest_quotes = [cex_quote, dex_quote]

        opp_a = self._build_opportunity(buy=cex_quote, sell=dex_quote)
        opp_b = self._build_opportunity(buy=dex_quote, sell=cex_quote)
        best = opp_a if opp_a.net_spread_bps >= opp_b.net_spread_bps else opp_b

        executable, reason = self.risk.validate(best, [cex_quote, dex_quote])
        best.executable = executable
        best.reason = reason

        self.opportunities.insert(0, best)
        self.opportunities = self.opportunities[:100]
        await save_opportunity(best)

        if best.executable:
            trade = await self.executor.execute(best)
            self.trades.insert(0, trade)
            self.trades = self.trades[:100]
            await save_trade(trade)

        return best

    def _build_opportunity(self, buy: Quote, sell: Quote) -> Opportunity:
        buy_price = buy.ask
        sell_price = sell.bid
        gross_spread_bps = ((sell_price - buy_price) / buy_price) * 10000
        trade_size_usd = min(settings.trade_size_usd, settings.max_trade_usd)
        fee_bps = settings.cex_fee_bps + settings.dex_fee_bps
        buffer_bps = settings.slippage_buffer_bps
        gas_bps = (settings.gas_buffer_usd / trade_size_usd) * 10000
        net_spread_bps = gross_spread_bps - fee_bps - buffer_bps - gas_bps
        expected_profit_usd = (net_spread_bps / 10000) * trade_size_usd
        direction = f"buy {buy.venue} / sell {sell.venue}"

        return Opportunity(
            ts=datetime.now(UTC),
            pair=settings.pair,
            direction=direction,
            buy_venue=buy.venue,
            sell_venue=sell.venue,
            buy_price=round(buy_price, 8),
            sell_price=round(sell_price, 8),
            gross_spread_bps=round(gross_spread_bps, 2),
            estimated_cost_usd=round(((fee_bps + buffer_bps + gas_bps) / 10000) * trade_size_usd, 4),
            net_spread_bps=round(net_spread_bps, 2),
            trade_size_usd=trade_size_usd,
            expected_profit_usd=round(expected_profit_usd, 4),
            executable=False,
            reason="not checked",
        )


scanner = ArbScanner()
