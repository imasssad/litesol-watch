import random
from datetime import datetime, UTC
from app.connectors.base import MarketConnector
from app.models.schemas import Quote


class MockCexConnector(MarketConnector):
    venue = "Mock CEX"

    def __init__(self, mid_price: float = 145.0):
        self.mid_price = mid_price

    async def get_quote(self, pair: str) -> Quote:
        drift = random.uniform(-0.25, 0.25)
        self.mid_price = max(1, self.mid_price + drift)
        spread = random.uniform(0.01, 0.05)
        return Quote(
            venue=self.venue,
            pair=pair,
            bid=round(self.mid_price - spread, 4),
            ask=round(self.mid_price + spread, 4),
            ts=datetime.now(UTC),
        )
