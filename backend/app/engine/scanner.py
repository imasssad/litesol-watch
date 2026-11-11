import asyncio
import logging
from datetime import UTC, datetime

from app.config import settings
from app.connectors.base import MarketConnector, RateLimited
from app.connectors.factory import build_cex_connector, build_dex_connector
from app.engine.executor import TradeExecutor
from app.engine.pricing import build_opportunity
from app.engine.risk import RiskEngine
from app.models.schemas import Opportunity, Quote, TradeRecord
from app.services.repository import (
    load_bot_state,
    realized_pnl_since,
    save_bot_state,
    save_opportunity,
    save_trade,
)

logger = logging.getLogger(__name__)

# Ceiling for rate-limit backoff, so a throttled venue cannot stall the
# scanner indefinitely.
MAX_BACKOFF_SECONDS = 300.0


class ArbScanner:
    def __init__(self):
        self.risk = RiskEngine()
        self.running = False
        self.latest_quotes: list[Quote] = []
        self.opportunities: list[Opportunity] = []
        self.trades: list[TradeRecord] = []
        self._task: asyncio.Task | None = None
        self._scan_lock = asyncio.Lock()
        self._cex: MarketConnector | None = None
        self._dex: MarketConnector | None = None
        self._executor: TradeExecutor | None = None
        self._persisted_state: tuple[bool, str | None] | None = None
        self._rate_limit_backoff = 0
        self._state_restored = False

    # Connectors are built lazily so that importing this module cannot fail and
    # so a misconfigured venue surfaces as a startup error, not an import error.
    def _ensure_connectors(self) -> None:
        if self._executor is None:
            self._cex = build_cex_connector()
            self._dex = build_dex_connector()
            self._executor = TradeExecutor(self._cex, self._dex, self.risk)

    @property
    def cex(self) -> MarketConnector:
        self._ensure_connectors()
        assert self._cex is not None
        return self._cex

    @property
    def dex(self) -> MarketConnector:
        self._ensure_connectors()
        assert self._dex is not None
        return self._dex

    @property
    def executor(self) -> TradeExecutor:
        self._ensure_connectors()
        assert self._executor is not None
        return self._executor

    async def restore_state(self) -> None:
        """Reload persisted kill-switch state and recompute today's live PnL.

        Called once at startup (idempotent). Without it a restart silently
        released the kill switch and zeroed the daily loss.
        """
        if self._state_restored:
            return
        self._state_restored = True
        state = await load_bot_state()
        start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        daily_pnl = await realized_pnl_since(start_of_day)
        self.risk.restore(
            kill_switch=bool(state.kill_switch) if state else False,
            reason=state.kill_switch_reason if state else None,
            daily_pnl_usd=daily_pnl,
        )
        self._persisted_state = (self.risk.kill_switch, self.risk.kill_switch_reason)
        logger.info(
            "state restored: kill_switch=%s daily_pnl=%.4f",
            self.risk.kill_switch,
            daily_pnl,
        )

    async def sync_risk_state(self) -> None:
        """Persist the kill switch whenever it changed since the last write."""
        current = (self.risk.kill_switch, self.risk.kill_switch_reason)
        if current == self._persisted_state:
            return
        await save_bot_state(kill_switch=current[0], reason=current[1])
        self._persisted_state = current

    async def start(self) -> None:
        if self.running:
            return
        await self.restore_state()
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

    @property
    def rate_limited(self) -> bool:
        return self._rate_limit_backoff > 0

    async def _loop(self) -> None:
        while self.running:
            delay = settings.scan_interval_seconds
            try:
                await self.scan_once()
                self._rate_limit_backoff = 0
            except asyncio.CancelledError:
                raise
            except RateLimited as exc:
                # Being throttled is not a trading failure: we are blind, not
                # broken. Back off exponentially and retry rather than burning
                # the consecutive-failure budget and tripping a kill switch
                # that a human then has to reset.
                self._rate_limit_backoff = min(self._rate_limit_backoff + 1, 6)
                delay = exc.retry_after or min(
                    settings.scan_interval_seconds * (2**self._rate_limit_backoff),
                    MAX_BACKOFF_SECONDS,
                )
                logger.warning(
                    "%s rate limited this client; backing off %.0fs "
                    "(not counted as a trading failure)",
                    exc.venue,
                    delay,
                )
            except Exception as exc:
                # Previously swallowed silently, which hid venue outages and
                # config errors behind a kill switch with no explanation.
                logger.exception("scan failed")
                self.risk.record_failure(f"scan error: {exc}")
            try:
                await self.sync_risk_state()
            except Exception:
                logger.exception("failed to persist risk state")
            await asyncio.sleep(delay)

    async def health(self) -> dict:
        cex: dict | BaseException
        dex: dict | BaseException
        cex, dex = await asyncio.gather(
            self.cex.health(), self.dex.health(), return_exceptions=True
        )

        def norm(venue: str, result: dict | BaseException) -> dict:
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
