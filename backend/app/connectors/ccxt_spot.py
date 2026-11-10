from datetime import datetime, UTC
from typing import Any

import ccxt.async_support as ccxt

from app.config import settings
from app.connectors.base import ExecutionConnector
from app.models.schemas import OrderResult, Quote, Side


class CcxtSpotConnector(ExecutionConnector):
    """Generic CCXT spot connector for CEXs supported by ccxt.

    Used for venues where native adapters are not yet custom-tuned. Keep live
    execution behind the same global LIVE_TRADING + EXECUTION_ENABLED gates.
    """

    exchange_id: str = ""
    venue: str = "CCXT Spot"

    def __init__(self, api_key: str = "", api_secret: str = "", password: str = "", sandbox: bool = False) -> None:
        if not self.exchange_id:
            raise ValueError("exchange_id is required")
        exchange_cls = getattr(ccxt, self.exchange_id)
        options: dict[str, Any] = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        if password:
            options["password"] = password
        self.exchange = exchange_cls(options)
        if sandbox and hasattr(self.exchange, "set_sandbox_mode"):
            self.exchange.set_sandbox_mode(True)

    async def close(self) -> None:
        await self.exchange.close()

    async def health(self) -> dict:
        try:
            await self.exchange.fetch_time()
            return {"venue": self.venue, "ok": True}
        except Exception as exc:
            return {"venue": self.venue, "ok": False, "error": str(exc)}

    async def get_quote(self, pair: str) -> Quote:
        ticker = await self.exchange.fetch_ticker(pair)
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is None or ask is None:
            order_book = await self.exchange.fetch_order_book(pair, limit=5)
            bid = order_book["bids"][0][0] if order_book.get("bids") else None
            ask = order_book["asks"][0][0] if order_book.get("asks") else None
        if bid is None or ask is None:
            raise RuntimeError(f"{self.venue} returned no usable bid/ask for {pair}")
        return Quote(
            venue=self.venue,
            pair=pair,
            bid=float(bid),
            ask=float(ask),
            ts=datetime.now(UTC),
            source=f"ccxt_{self.exchange_id}",
        )

    async def market_order(self, pair: str, side: Side, quote_size_usd: float) -> OrderResult:
        if not settings.live_execution_allowed:
            raise RuntimeError("live execution is disabled")
        if not self.exchange.apiKey or not self.exchange.secret:
            raise RuntimeError(f"missing {self.venue} API credentials")

        quote = await self.get_quote(pair)
        price = quote.ask if side == Side.buy else quote.bid
        amount = quote_size_usd / max(price, 0.00000001)

        market = await self.exchange.load_markets()
        if pair not in market:
            raise RuntimeError(f"{pair} is not available on {self.venue}")
        amount = float(self.exchange.amount_to_precision(pair, amount))

        order = await self.exchange.create_order(pair, "market", side.value, amount)
        filled_qty = float(order.get("filled") or order.get("amount") or 0)
        avg_price = float(order.get("average") or price)
        return OrderResult(
            venue=self.venue,
            order_id=str(order.get("id")) if order.get("id") is not None else None,
            status=str(order.get("status") or "submitted"),
            side=side,
            pair=pair,
            filled_qty=filled_qty,
            avg_price=avg_price,
            raw=order,
        )
