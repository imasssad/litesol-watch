from app.config import settings
from app.connectors.ccxt_spot import CcxtSpotConnector


class MexcSpotConnector(CcxtSpotConnector):
    exchange_id = "mexc"
    venue = "MEXC Spot"

    def __init__(self) -> None:
        super().__init__(
            api_key=settings.mexc_api_key,
            api_secret=settings.mexc_api_secret,
            sandbox=settings.mexc_sandbox,
        )
