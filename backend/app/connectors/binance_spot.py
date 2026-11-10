import hashlib
import hmac
import logging
import math
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.connectors.base import ExecutionConnector
from app.models.schemas import OrderResult, Quote, Side

logger = logging.getLogger(__name__)


class BinanceSpotConnector(ExecutionConnector):
    venue = "Binance Spot"

    def __init__(self) -> None:
        self.base_url = (
            "https://testnet.binance.vision"
            if settings.binance_testnet
            else settings.binance_base_url
        )
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=5.0)
        self._filters: dict | None = None
        self._time_offset_ms: int | None = None
        self._time_synced_at: float = 0.0

    async def aclose(self) -> None:
        await self.client.aclose()

    async def health(self) -> dict:
        # Must not raise: /api/health aggregates this and a bare exception turned
        # the whole endpoint into a 500 whenever Binance was unreachable.
        try:
            r = await self.client.get("/api/v3/ping")
            return {
                "venue": self.venue,
                "ok": r.status_code == 200,
                "status_code": r.status_code,
            }
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return {"venue": self.venue, "ok": False, "error": str(exc)}

    async def get_quote(self, pair: str) -> Quote:
        symbol = settings.symbol_binance
        r = await self.client.get("/api/v3/ticker/bookTicker", params={"symbol": symbol})
        r.raise_for_status()
        data = r.json()
        bid = float(data["bidPrice"])
        ask = float(data["askPrice"])
        if bid <= 0 or ask <= 0:
            raise RuntimeError(f"{self.venue} returned a non-positive book for {symbol}")
        return Quote(
            venue=self.venue,
            pair=pair,
            bid=bid,
            ask=ask,
            ts=datetime.now(UTC),
            source="binance_rest",
        )

    async def _symbol_filters(self) -> dict:
        """LOT_SIZE / NOTIONAL rules for the traded symbol, fetched once.

        Without these, a quantity formatted to a fixed 6 decimals is rejected by
        Binance with -1111 (precision over maximum) on most symbols.
        """
        if self._filters is not None:
            return self._filters
        symbol = settings.symbol_binance
        r = await self.client.get("/api/v3/exchangeInfo", params={"symbol": symbol})
        r.raise_for_status()
        symbols = r.json().get("symbols") or []
        if not symbols:
            raise RuntimeError(f"{symbol} is not listed on {self.venue}")
        filters = {f["filterType"]: f for f in symbols[0].get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        self._filters = {
            "step_size": float(lot.get("stepSize", 0) or 0),
            "min_qty": float(lot.get("minQty", 0) or 0),
            "min_notional": float(notional.get("minNotional", 0) or 0),
        }
        return self._filters

    @staticmethod
    def _round_step(qty: float, step: float) -> tuple[float, str]:
        if step <= 0:
            return qty, f"{qty:.8f}".rstrip("0").rstrip(".")
        rounded = math.floor(qty / step) * step
        decimals = max(0, -math.floor(math.log10(step)) if step < 1 else 0)
        return rounded, f"{rounded:.{decimals}f}"

    async def _timestamp_ms(self) -> int:
        """Order timestamp pinned to Binance server time.

        Signing with the local clock means any skew beyond recvWindow rejects
        every order with -1021. The offset is refreshed every 30 minutes; if
        the sync call fails we fall back to the local clock and retry in ~5
        minutes rather than refusing to trade on a healthy clock.
        """
        now = time.time()
        if self._time_offset_ms is None or now - self._time_synced_at > 1800:
            try:
                r = await self.client.get("/api/v3/time")
                r.raise_for_status()
                server_ms = int(r.json()["serverTime"])
                self._time_offset_ms = server_ms - int(time.time() * 1000)
                self._time_synced_at = now
                if abs(self._time_offset_ms) > 1000:
                    logger.warning(
                        "local clock is %+d ms from Binance server time",
                        -self._time_offset_ms,
                    )
            except Exception:  # noqa: BLE001 - degrade to local clock, retry soon
                logger.warning("Binance time sync failed; using local clock")
                if self._time_offset_ms is None:
                    self._time_offset_ms = 0
                self._time_synced_at = now - 1500  # retry in ~5 minutes
        return int(time.time() * 1000) + self._time_offset_ms

    async def market_order(
        self, pair: str, side: Side, quote_size_usd: float
    ) -> OrderResult:
        if not settings.live_execution_allowed:
            raise RuntimeError("live execution is disabled")
        if not settings.binance_api_key or not settings.binance_api_secret:
            raise RuntimeError("missing Binance API credentials")

        symbol = settings.symbol_binance
        params: dict[str, str | int] = {
            "symbol": symbol,
            "side": side.value.upper(),
            "type": "MARKET",
            "newOrderRespType": "FULL",
            "recvWindow": settings.binance_recv_window_ms,
            "timestamp": await self._timestamp_ms(),
        }

        if side == Side.buy:
            params["quoteOrderQty"] = f"{quote_size_usd:.2f}"
        else:
            quote = await self.get_quote(pair)
            filters = await self._symbol_filters()
            raw_qty = quote_size_usd / quote.bid
            qty, qty_str = self._round_step(raw_qty, filters["step_size"])
            if qty <= 0 or qty < filters["min_qty"]:
                raise RuntimeError(
                    f"sell qty {raw_qty:.8f} rounds to {qty} which is below "
                    f"minQty {filters['min_qty']} for {symbol}"
                )
            if filters["min_notional"] and qty * quote.bid < filters["min_notional"]:
                raise RuntimeError(
                    f"sell notional {qty * quote.bid:.4f} is below minNotional "
                    f"{filters['min_notional']} for {symbol}"
                )
            params["quantity"] = qty_str

        # Sign the exact query string that is sent. Re-serialising the dict in the
        # request layer risks a signature over different bytes than the server sees.
        query = urlencode(params)
        sig = hmac.new(
            settings.binance_api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        headers = {"X-MBX-APIKEY": settings.binance_api_key}
        r = await self.client.post(
            f"/api/v3/order?{query}&signature={sig}", headers=headers
        )
        if r.status_code >= 400:
            raise RuntimeError(f"{self.venue} order rejected ({r.status_code}): {r.text}")
        data = r.json()

        fills = data.get("fills") or []
        filled_qty = float(data.get("executedQty", 0) or 0)
        notional = float(data.get("cummulativeQuoteQty", 0) or 0)
        if notional <= 0 and fills:
            notional = sum(
                float(f.get("price", 0)) * float(f.get("qty", 0)) for f in fills
            )
        avg_price = notional / filled_qty if filled_qty else 0.0
        return OrderResult(
            venue=self.venue,
            order_id=str(data.get("orderId")) if data.get("orderId") is not None else None,
            status=data.get("status", "UNKNOWN"),
            side=side,
            pair=pair,
            filled_qty=filled_qty,
            avg_price=avg_price,
            raw=data,
        )
