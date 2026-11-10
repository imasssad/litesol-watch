from datetime import datetime, UTC
import httpx
from app.config import settings
from app.connectors.base import ExecutionConnector
from app.models.schemas import OrderResult, Quote, Side

MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkYccWJyYq7cWTQw",
}
DECIMALS = {"SOL": 9, "USDC": 6, "USDT": 6}


class JupiterConnector(ExecutionConnector):
    venue = "Jupiter"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=8.0)

    async def health(self) -> dict:
        try:
            await self.get_quote(settings.pair)
            return {"venue": self.venue, "ok": True}
        except Exception as exc:
            return {"venue": self.venue, "ok": False, "error": str(exc)}

    async def get_quote(self, pair: str) -> Quote:
        base = settings.base_asset
        quote = settings.quote_asset
        amount = int(settings.trade_size_usd * (10 ** DECIMALS[quote]))
        # Quote USDC -> base, then infer base ask. Quote base -> USDC for bid.
        buy_params = {
            "inputMint": MINTS[quote],
            "outputMint": MINTS[base],
            "amount": amount,
            "slippageBps": int(settings.max_slippage_bps),
        }
        buy_r = await self.client.get(settings.jupiter_quote_url, params=buy_params)
        buy_r.raise_for_status()
        buy_data = buy_r.json()
        out_base = float(buy_data["outAmount"]) / (10 ** DECIMALS[base])
        ask = settings.trade_size_usd / max(out_base, 0.000000001)

        base_amount = int(out_base * (10 ** DECIMALS[base]))
        sell_params = {
            "inputMint": MINTS[base],
            "outputMint": MINTS[quote],
            "amount": base_amount,
            "slippageBps": int(settings.max_slippage_bps),
        }
        sell_r = await self.client.get(settings.jupiter_quote_url, params=sell_params)
        sell_r.raise_for_status()
        sell_data = sell_r.json()
        out_quote = float(sell_data["outAmount"]) / (10 ** DECIMALS[quote])
        bid = out_quote / max(out_base, 0.000000001)
        return Quote(
            venue=self.venue,
            pair=pair,
            bid=round(bid, 8),
            ask=round(ask, 8),
            ts=datetime.now(UTC),
            source="jupiter_quote_api",
        )

    async def market_order(self, pair: str, side: Side, quote_size_usd: float) -> OrderResult:
        # Production guard: building and broadcasting Solana transactions needs wallet custody/signing.
        # This method deliberately refuses unless a signing implementation is added and audited.
        # Keep DEX execution behind an explicit adapter boundary to support Fireblocks, MPC, HSM, or local signer.
        if not settings.live_execution_allowed:
            raise RuntimeError("live execution is disabled")
        raise NotImplementedError(
            "Jupiter live swap signing/broadcast is intentionally not enabled in this scaffold. "
            "Implement with an audited signer or custody provider before using funds."
        )
