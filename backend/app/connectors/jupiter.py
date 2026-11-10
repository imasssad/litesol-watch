from datetime import datetime, UTC

import httpx

from app.config import settings
from app.connectors.base import ExecutionConnector
from app.models.schemas import OrderResult, Quote, Side

# SPL mint addresses. Each is a 32-byte base58 pubkey; verify any addition
# against an authoritative source before trading it.
MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkYccWJyYq7cWTQw",
}
DECIMALS = {"SOL": 9, "USDC": 6, "USDT": 6}

MIN_OUT = 1e-12


class JupiterConnector(ExecutionConnector):
    venue = "Jupiter"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=8.0)

    async def aclose(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _asset(symbol: str) -> tuple[str, int]:
        """Resolve an asset symbol to (mint, decimals) with a clear error."""
        if symbol not in MINTS or symbol not in DECIMALS:
            raise RuntimeError(
                f"asset {symbol!r} has no known Solana mint/decimals. "
                f"Supported: {', '.join(sorted(MINTS))}. "
                "Add it to app/connectors/jupiter.py to trade it."
            )
        return MINTS[symbol], DECIMALS[symbol]

    async def health(self) -> dict:
        try:
            await self.get_quote(settings.pair)
            return {"venue": self.venue, "ok": True}
        except Exception as exc:
            return {"venue": self.venue, "ok": False, "error": str(exc)}

    async def get_quote(self, pair: str) -> Quote:
        base_mint, base_dec = self._asset(settings.base_asset)
        quote_mint, quote_dec = self._asset(settings.quote_asset)

        # Quote at the size actually traded, so the depth sampled here matches
        # what execution would consume.
        size_usd = settings.effective_trade_size_usd
        amount = int(size_usd * (10**quote_dec))
        if amount <= 0:
            raise RuntimeError(f"trade size {size_usd} is too small to quote")

        # Quote quote->base to infer the base ask, then base->quote for the bid.
        buy_params = {
            "inputMint": quote_mint,
            "outputMint": base_mint,
            "amount": amount,
            "slippageBps": int(settings.max_slippage_bps),
        }
        buy_r = await self.client.get(settings.jupiter_quote_url, params=buy_params)
        buy_r.raise_for_status()
        buy_data = buy_r.json()
        out_base = float(buy_data["outAmount"]) / (10**base_dec)
        if out_base <= MIN_OUT:
            raise RuntimeError(f"{self.venue} returned no routable size for {pair}")
        ask = size_usd / out_base

        base_amount = int(out_base * (10**base_dec))
        sell_params = {
            "inputMint": base_mint,
            "outputMint": quote_mint,
            "amount": base_amount,
            "slippageBps": int(settings.max_slippage_bps),
        }
        sell_r = await self.client.get(settings.jupiter_quote_url, params=sell_params)
        sell_r.raise_for_status()
        sell_data = sell_r.json()
        out_quote = float(sell_data["outAmount"]) / (10**quote_dec)
        bid = out_quote / out_base
        if bid <= 0 or ask <= 0:
            raise RuntimeError(f"{self.venue} produced a non-positive book for {pair}")
        return Quote(
            venue=self.venue,
            pair=pair,
            bid=round(bid, 8),
            ask=round(ask, 8),
            ts=datetime.now(UTC),
            source="jupiter_quote_api",
        )

    async def market_order(
        self, pair: str, side: Side, quote_size_usd: float
    ) -> OrderResult:
        # Production guard: building and broadcasting Solana transactions needs
        # wallet custody/signing. This method deliberately refuses unless a signing
        # implementation is added and audited. Keeping DEX execution behind an
        # explicit adapter boundary supports Fireblocks, MPC, HSM, or a local signer.
        if not settings.live_execution_allowed:
            raise RuntimeError("live execution is disabled")
        raise NotImplementedError(
            "Jupiter live swap signing/broadcast is intentionally not enabled in "
            "this scaffold. Implement with an audited signer or custody provider "
            "before using funds."
        )
