from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Arb Bot Production v1"
    environment: str = "local"
    api_auth_token: str = "change-me-long-random-token"

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
    database_url: str = "sqlite+aiosqlite:///./arb_bot.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def symbol_binance(self) -> str:
        return f"{self.base_asset}{self.quote_asset}"

    @property
    def symbol_ccxt(self) -> str:
        return f"{self.base_asset}/{self.quote_asset}"

    @property
    def live_execution_allowed(self) -> bool:
        return self.live_trading and self.execution_enabled


settings = Settings()
