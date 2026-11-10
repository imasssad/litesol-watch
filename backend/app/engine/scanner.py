import asyncio
import logging

from app.config import settings
from app.connectors.factory import build_cex_connector, build_dex_connector
from app.engine.executor import TradeExecutor
from app.engine.pricing import build_opportunity
from app.engine.risk import RiskEngine
from app.models.schemas import Opportunity, Quote, TradeRecord
from app.services.repository import save_opportunity, save_trade

logger = logging.getLogger(__name__)


class ArbScanner:
    def __init__(self):
        self.risk = RiskEngine()
        self.running = False
        self.latest_quotes: list[Quote] = []
        self.opportunities: list[Opportunity] = []
        self.trades: list[TradeRecord] = []
        self._task: asyncio.Task | None = None
        self._scan_lock = asyncio.Lock()
        self._cex = None
        self._dex = None
        self._executor: TradeExecutor | None = None

    # Connectors are built lazily so that importing this module cannot fail and
    # so a misconfigured venue surfaces as a startup error, not an import error.
    def _ensure_connectors(self) -> None:
        if self._executor is None:
            self._cex = build_cex_connector()
            self._dex = build_dex_connector()
            self._executor = TradeExecutor(self._cex, self._dex, self.risk)

    @property
    def cex(self):
        self._ensure_connectors()
        return self._cex

    @property
    def dex(self):
        self._ensure_connectors()
        return self._dex

    @property
    def executor(self) -> TradeExecutor:
        self._ensure_connectors()
        return self._executor

    async def start(self) -> None:
        if self.running:
            return
        self._ensure_connectors()
        self.running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "scanner started: pair=%s cex=%s dex=%s live_execution=%s",
            settings.pair,
            self.cex.venue,
            self.dex.venue,
            settings.live_execution_allowed,
        )

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("scanner stopped")

    async def close(self) -> None:
        """Release connector sockets (httpx clients, CCXT aiohttp session)."""
        await self.stop()
        for connector in (self._cex, self._dex):
            if connector is None:
                continue
            closer = getattr(connector, "aclose", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception:
                logger.exception("failed to close connector %s", connector.venue)
        self._cex = self._dex = self._executor = None

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Previously swallowed silently, which hid venue outages and
                # config errors behind a kill switch with no explanation.
                logger.exception("scan failed")
                self.risk.record_failure(f"scan error: {exc}")
            await asyncio.sleep(settings.scan_interval_seconds)

    async def health(self) -> dict:
        cex, dex = await asyncio.gather(
            self.cex.health(), self.dex.health(), return_exceptions=True
        )

        def norm(venue, result):
            if isinstance(result, BaseException):
                return {"venue": venue, "ok": False, "error": str(result)}
            return result

        return {
            "cex": norm(self.cex.venue, cex),
            "dex": norm(self.dex.venue, dex),
        }

    async def scan_once(self) -> Opportunity:
        # The background loop and POST /api/scan-once must not interleave, or two
        # scans can each decide to execute on the same edge.
        async with self._scan_lock:
            return await self._scan_once_unlocked()

    async def _scan_once_unlocked(self) -> Opportunity:
        cex_quote, dex_quote = await asyncio.gather(
            self.cex.get_quote(settings.pair),
            self.dex.get_quote(settings.pair),
        )
        self.latest_quotes = [cex_quote, dex_quote]

        opp_a = build_opportunity(buy=cex_quote, sell=dex_quote)
        opp_b = build_opportunity(buy=dex_quote, sell=cex_quote)
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


scanner = ArbScanner()
