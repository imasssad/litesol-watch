from abc import ABC, abstractmethod

from app.models.schemas import OrderResult, Quote, Side


class MarketConnector(ABC):
    venue: str

    @abstractmethod
    async def get_quote(self, pair: str) -> Quote:
        raise NotImplementedError

    async def health(self) -> dict:
        return {"venue": self.venue, "ok": True}

    async def aclose(self) -> None:
        """Release any network resources. Overridden where sockets are held."""
        return None


class ExecutionConnector(MarketConnector):
    @abstractmethod
    async def market_order(
        self, pair: str, side: Side, quote_size_usd: float
    ) -> OrderResult:
        raise NotImplementedError
