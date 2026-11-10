from app.config import settings
from app.connectors.binance_spot import BinanceSpotConnector
from app.connectors.bingx_spot import BingXSpotConnector
from app.connectors.jupiter import JupiterConnector
from app.connectors.mexc_spot import MexcSpotConnector
from app.connectors.mock_cex import MockCexConnector
from app.connectors.mock_dex import MockDexConnector

CEX_BUILDERS = {
    "binance": BinanceSpotConnector,
    "mexc": MexcSpotConnector,
    "bingx": BingXSpotConnector,
    "bing_x": BingXSpotConnector,
    # Offline price source so the whole stack can be exercised without venue
    # access. Paper mode only; it refuses to place orders.
    "mock": MockCexConnector,
}

DEX_BUILDERS = {
    "jupiter": JupiterConnector,
    "mock": MockDexConnector,
}


def build_cex_connector():
    venue = settings.cex_venue.lower().strip()
    builder = CEX_BUILDERS.get(venue)
    if builder is None:
        raise ValueError(
            f"Unsupported CEX_VENUE={settings.cex_venue!r}. "
            f"Use one of: {', '.join(sorted(CEX_BUILDERS))}."
        )
    return builder()


def build_dex_connector():
    venue = settings.dex_venue.lower().strip()
    builder = DEX_BUILDERS.get(venue)
    if builder is None:
        raise ValueError(
            f"Unsupported DEX_VENUE={settings.dex_venue!r}. "
            f"Use one of: {', '.join(sorted(DEX_BUILDERS))}."
        )
    return builder()
