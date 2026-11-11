from datetime import UTC, datetime

import httpx

from app.config import settings
from app.connectors.base import ExecutionConnector, RateLimited
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
        headers = {}
        if settings.jupiter_api_key:
            # portal.jup.ag keys go in x-api-key (api.jup.ag host). The keyless
            # lite-api host ignores the header.
            headers["x-api-key"] = settings.jupiter_api_key
        self.client = httpx.AsyncClient(timeout=8.0, headers=headers)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _get_json(self, url: str, params: dict) -> dict:
        """GET that turns a 429 into RateLimited, honouring Retry-After."""
        r = await self.client.get(url, params=params)
        if r.status_code == 429:
            raw = r.headers.get("Retry-After", "")
            try:
                retry_after = float(raw) if raw else None
            except ValueError:
                retry_after = None
            raise RateLimited(self.venue, retry_after=retry_after)
        r.raise_for_status()
        return r.json()

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
        except Exception as exc:  # noqa: BLE001 - health must never raise
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
        buy_params: dict[str, str | int] = {
            "inputMint": quote_mint,
            "outputMint": base_mint,
            "amount": amount,
            "slippageBps": int(settings.max_slippage_bps),
        }
        buy_data = await self._get_json(settings.jupiter_quote_url, buy_params)
        out_base = float(buy_data["outAmount"]) / (10**base_dec)
        if out_base <= MIN_OUT:
            raise RuntimeError(f"{self.venue} returned no routable size for {pair}")
        ask = size_usd / out_base

        base_amount = int(out_base * (10**base_dec))
        sell_params: dict[str, str | int] = {
            "inputMint": base_mint,
            "outputMint": quote_mint,
            "amount": base_amount,
            "slippageBps": int(settings.max_slippage_bps),
        }
        sell_data = await self._get_json(settings.jupiter_quote_url, sell_params)
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
