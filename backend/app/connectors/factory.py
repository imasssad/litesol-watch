from app.config import settings
from app.connectors.binance_spot import BinanceSpotConnector
from app.connectors.mexc_spot import MexcSpotConnector
from app.connectors.bingx_spot import BingXSpotConnector


def build_cex_connector():
    venue = settings.cex_venue.lower().strip()
    if venue == "binance":
        return BinanceSpotConnector()
    if venue == "mexc":
        return MexcSpotConnector()
    if venue in {"bingx", "bing_x"}:
        return BingXSpotConnector()
    raise ValueError(f"Unsupported CEX_VENUE={settings.cex_venue}. Use binance, mexc, or bingx.")
