from app.config import settings
from app.connectors.ccxt_spot import CcxtSpotConnector


class BingXSpotConnector(CcxtSpotConnector):
    exchange_id = "bingx"
    venue = "BingX Spot"

    def __init__(self) -> None:
        super().__init__(
            api_key=settings.bingx_api_key,
            api_secret=settings.bingx_api_secret,
            password=settings.bingx_api_password,
            sandbox=settings.bingx_sandbox,
        )
