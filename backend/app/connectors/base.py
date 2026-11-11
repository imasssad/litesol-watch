from abc import ABC, abstractmethod

from app.models.schemas import OrderResult, Quote, Side


class RateLimited(RuntimeError):
    """A venue asked us to slow down (HTTP 429, or a CCXT rate-limit error).

    Deliberately NOT a trading failure. Being rate limited means we are
    temporarily blind, not that the venue or our logic is broken, so the
    scanner backs off instead of spending its consecutive-failure budget and
    tripping a kill switch that then needs a manual reset.
    """

    def __init__(
        self, venue: str, retry_after: float | None = None, message: str = ""
    ) -> None:
        self.venue = venue
        self.retry_after = retry_after
        super().__init__(message or f"{venue} rate limited this client")


class MarketConnector(ABC):
    venue: str

    @abstractmethod
    async def get_quote(self, pair: str) -> Quote:
        raise NotImplementedError

    async def health(self) -> dict:
        return {"venue": self.venue, "ok": True}

    async def aclose(self) -> None:
        """Release any network resources. Overridden where sockets are held."""
        return


class ExecutionConnector(MarketConnector):
    @abstractmethod
    async def market_order(
        self, pair: str, side: Side, quote_size_usd: float
    ) -> OrderResult:
        raise NotImplementedError
