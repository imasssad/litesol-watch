import hashlib
import hmac
import time
from datetime import datetime, UTC
from urllib.parse import urlencode
import httpx
from app.config import settings
from app.connectors.base import ExecutionConnector
from app.models.schemas import OrderResult, Quote, Side


class BinanceSpotConnector(ExecutionConnector):
    venue = "Binance Spot"

    def __init__(self) -> None:
        self.base_url = "https://testnet.binance.vision" if settings.binance_testnet else settings.binance_base_url
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=5.0)

    async def health(self) -> dict:
        r = await self.client.get("/api/v3/ping")
        return {"venue": self.venue, "ok": r.status_code == 200, "status_code": r.status_code}

    async def get_quote(self, pair: str) -> Quote:
        symbol = settings.symbol_binance
        r = await self.client.get("/api/v3/ticker/bookTicker", params={"symbol": symbol})
        r.raise_for_status()
        data = r.json()
        return Quote(
            venue=self.venue,
            pair=pair,
            bid=float(data["bidPrice"]),
            ask=float(data["askPrice"]),
            ts=datetime.now(UTC),
            source="binance_rest",
        )

    async def market_order(self, pair: str, side: Side, quote_size_usd: float) -> OrderResult:
        if not settings.live_execution_allowed:
            raise RuntimeError("live execution is disabled")
        if not settings.binance_api_key or not settings.binance_api_secret:
            raise RuntimeError("missing Binance API credentials")
        symbol = settings.symbol_binance
        params = {
            "symbol": symbol,
            "side": side.value.upper(),
            "type": "MARKET",
            "timestamp": int(time.time() * 1000),
        }
        if side == Side.buy:
            params["quoteOrderQty"] = f"{quote_size_usd:.2f}"
        else:
            quote = await self.get_quote(pair)
            qty = quote_size_usd / max(quote.bid, 0.00000001)
            params["quantity"] = f"{qty:.6f}"
        body = urlencode(params)
        sig = hmac.new(settings.binance_api_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers = {"X-MBX-APIKEY": settings.binance_api_key}
        r = await self.client.post("/api/v3/order", params={**params, "signature": sig}, headers=headers)
        r.raise_for_status()
        data = r.json()
        fills = data.get("fills", [])
        filled_qty = float(data.get("executedQty", 0) or 0)
        notional = sum(float(f.get("price", 0)) * float(f.get("qty", 0)) for f in fills)
        avg_price = notional / filled_qty if filled_qty else 0
        return OrderResult(
            venue=self.venue,
            order_id=str(data.get("orderId")),
            status=data.get("status", "UNKNOWN"),
            side=side,
            pair=pair,
            filled_qty=filled_qty,
            avg_price=avg_price,
            raw=data,
        )
