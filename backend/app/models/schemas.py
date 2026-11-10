from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class Side(StrEnum):
    buy = "buy"
    sell = "sell"


class Quote(BaseModel):
    venue: str
    pair: str
    bid: float
    ask: float
    ts: datetime
    source: str = "live"


class Opportunity(BaseModel):
    ts: datetime
    pair: str
    direction: str
    buy_venue: str
    sell_venue: str
    buy_price: float
    sell_price: float
    gross_spread_bps: float
    estimated_cost_usd: float
    net_spread_bps: float
    trade_size_usd: float
    expected_profit_usd: float
    executable: bool
    reason: str


class TradeRecord(BaseModel):
    ts: datetime
    pair: str
    direction: str
    buy_venue: str
    sell_venue: str
    trade_size_usd: float
    expected_profit_usd: float
    realized_profit_usd: float | None = None
    status: str = "paper_filled"
    mode: str = "paper"
    error: str | None = None
    buy_order_id: str | None = None
    sell_order_id: str | None = None


class OrderResult(BaseModel):
    venue: str
    order_id: str | None = None
    status: str
    side: Side
    pair: str
    filled_qty: float = 0
    avg_price: float = 0
    raw: dict | None = None


PaperTrade = TradeRecord
