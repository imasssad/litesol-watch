from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OpportunityRow(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    pair: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(128))
    buy_venue: Mapped[str] = mapped_column(String(64))
    sell_venue: Mapped[str] = mapped_column(String(64))
    buy_price: Mapped[float] = mapped_column(Float)
    sell_price: Mapped[float] = mapped_column(Float)
    gross_spread_bps: Mapped[float] = mapped_column(Float)
    estimated_cost_usd: Mapped[float] = mapped_column(Float)
    net_spread_bps: Mapped[float] = mapped_column(Float)
    trade_size_usd: Mapped[float] = mapped_column(Float)
    expected_profit_usd: Mapped[float] = mapped_column(Float)
    executable: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)


class TradeRow(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    pair: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(128))
    buy_venue: Mapped[str] = mapped_column(String(64))
    sell_venue: Mapped[str] = mapped_column(String(64))
    trade_size_usd: Mapped[float] = mapped_column(Float)
    expected_profit_usd: Mapped[float] = mapped_column(Float)
    realized_profit_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    buy_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sell_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class BotStateRow(Base):
    """Single-row (id=1) operational state that must survive a restart.

    Without it, a restart silently released the kill switch and forgot the
    day's realized loss.
    """

    __tablename__ = "bot_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
