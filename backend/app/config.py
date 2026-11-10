import logging
import secrets
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

PLACEHOLDER_API_TOKEN = "change-me-long-random-token"
SUPPORTED_CEX_VENUES = {"binance", "mexc", "bingx", "mock"}
SUPPORTED_DEX_VENUES = {"jupiter", "mock"}


class Settings(BaseSettings):
    app_name: str = "Arb Bot Production v1"
    environment: str = "local"
    api_auth_token: str = PLACEHOLDER_API_TOKEN

    live_trading: bool = False
    execution_enabled: bool = False

    pair: str = "SOL/USDC"
    base_asset: str = "SOL"
    quote_asset: str = "USDC"
    trade_size_usd: float = 100
    min_net_spread_bps: float = 65
    max_trade_usd: float = 250
    max_daily_loss_usd: float = 100
    max_slippage_bps: float = 35
    max_quote_age_seconds: float = 2.0
    max_consecutive_failures: int = 3
    scan_interval_seconds: float = 2

    cex_venue: str = "binance"
    cex_fee_bps: float = 10
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_base_url: str = "https://api.binance.com"
    binance_testnet: bool = False
    binance_recv_window_ms: int = 5000

    mexc_api_key: str = ""
    mexc_api_secret: str = ""
    mexc_sandbox: bool = False

    bingx_api_key: str = ""
    bingx_api_secret: str = ""
    bingx_api_password: str = ""
    bingx_sandbox: bool = False

    dex_venue: str = "jupiter"
    dex_fee_bps: float = 5
    jupiter_quote_url: str = "https://quote-api.jup.ag/v6/quote"
    jupiter_swap_url: str = "https://quote-api.jup.ag/v6/swap"
    solana_rpc_url: str = ""
    solana_wallet_public_key: str = ""
    solana_private_key_base64: str = ""

    gas_buffer_usd: float = 0.05
    slippage_buffer_bps: float = 25
    database_url: str = "sqlite+aiosqlite:///./data/arb_bot.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        # `pair` is used for display and for the CCXT symbol, while base/quote drive
        # the Binance symbol and the Jupiter mints. If they disagree the bot quotes
        # one market and trades another, so refuse to start instead.
        expected_pair = f"{self.base_asset}/{self.quote_asset}".upper()
        if self.pair.replace(" ", "").upper() != expected_pair:
            raise ValueError(
                f"PAIR={self.pair!r} does not match BASE_ASSET/QUOTE_ASSET "
                f"({expected_pair}). Set PAIR={expected_pair} or fix the assets."
            )

        venue = self.cex_venue.lower().strip()
        if venue not in SUPPORTED_CEX_VENUES:
            raise ValueError(
                f"CEX_VENUE={self.cex_venue!r} is not supported. "
                f"Use one of: {', '.join(sorted(SUPPORTED_CEX_VENUES))}."
            )
        dex = self.dex_venue.lower().strip()
        if dex not in SUPPORTED_DEX_VENUES:
            raise ValueError(
                f"DEX_VENUE={self.dex_venue!r} is not supported. "
                f"Use one of: {', '.join(sorted(SUPPORTED_DEX_VENUES))}."
            )

        for name in ("trade_size_usd", "max_trade_usd"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name.upper()} must be greater than 0.")
        if self.scan_interval_seconds <= 0:
            raise ValueError("SCAN_INTERVAL_SECONDS must be greater than 0.")
        if self.max_quote_age_seconds <= 0:
            raise ValueError("MAX_QUOTE_AGE_SECONDS must be greater than 0.")
        if self.max_consecutive_failures < 1:
            raise ValueError("MAX_CONSECUTIVE_FAILURES must be at least 1.")

        if self.trade_size_usd > self.max_trade_usd:
            logger.warning(
                "TRADE_SIZE_USD=%s exceeds MAX_TRADE_USD=%s; trades will be capped at %s.",
                self.trade_size_usd,
                self.max_trade_usd,
                self.max_trade_usd,
            )

        # Live execution with the shipped placeholder token would mean an open,
        # unauthenticated kill switch on a bot that can move real funds.
        if self.live_execution_allowed and self.api_auth_token == PLACEHOLDER_API_TOKEN:
            raise ValueError(
                "API_AUTH_TOKEN is still the placeholder value while live execution is "
                "enabled. Set a long random API_AUTH_TOKEN before enabling "
                "LIVE_TRADING + EXECUTION_ENABLED."
            )
        return self

    @property
    def auth_configured(self) -> bool:
        return bool(self.api_auth_token) and self.api_auth_token != PLACEHOLDER_API_TOKEN

    @property
    def effective_trade_size_usd(self) -> float:
        """The size actually traded: never more than MAX_TRADE_USD."""
        return min(self.trade_size_usd, self.max_trade_usd)

    @property
    def symbol_binance(self) -> str:
        return f"{self.base_asset}{self.quote_asset}"

    @property
    def symbol_ccxt(self) -> str:
        return f"{self.base_asset}/{self.quote_asset}"

    @property
    def live_execution_allowed(self) -> bool:
        return self.live_trading and self.execution_enabled

    def verify_api_token(self, candidate: str | Any) -> bool:
        if not self.auth_configured or not isinstance(candidate, str):
            return False
        return secrets.compare_digest(candidate, self.api_auth_token)


settings = Settings()
