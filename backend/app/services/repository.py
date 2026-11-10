from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.models import BotStateRow, OpportunityRow, TradeRow
from app.db.session import AsyncSessionLocal
from app.models.schemas import Opportunity, TradeRecord


def _row_to_dict(row, table) -> dict:
    """Project a row onto its schema fields, normalising timestamps to UTC.

    SQLite has no native timestamp-with-offset type, so SQLAlchemy hands back a
    naive datetime even for a DateTime(timezone=True) column. Left alone, the
    API would emit timestamps with no zone and clients would read stored UTC as
    local time.
    """
    data = {c.name: getattr(row, c.name) for c in table.columns if c.name != "id"}
    ts = data.get("ts")
    if isinstance(ts, datetime) and ts.tzinfo is None:
        data["ts"] = ts.replace(tzinfo=UTC)
    return data


async def save_opportunity(opp: Opportunity) -> None:
    async with AsyncSessionLocal() as session:
        session.add(OpportunityRow(**opp.model_dump()))
        await session.commit()


async def save_trade(trade: TradeRecord) -> None:
    async with AsyncSessionLocal() as session:
        session.add(TradeRow(**trade.model_dump()))
        await session.commit()


async def recent_opportunities(limit: int = 50) -> list[Opportunity]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(OpportunityRow).order_by(OpportunityRow.id.desc()).limit(limit)
        )
        rows = result.scalars().all()
        return [
            Opportunity.model_validate(_row_to_dict(r, OpportunityRow.__table__))
            for r in rows
        ]


async def recent_trades(limit: int = 50) -> list[TradeRecord]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TradeRow).order_by(TradeRow.id.desc()).limit(limit)
        )
        rows = result.scalars().all()
        return [
            TradeRecord.model_validate(_row_to_dict(r, TradeRow.__table__)) for r in rows
        ]


async def save_bot_state(kill_switch: bool, reason: str | None) -> None:
    """Upsert the single persisted operational-state row (id=1)."""
    async with AsyncSessionLocal() as session:
        row = await session.get(BotStateRow, 1)
        if row is None:
            row = BotStateRow(id=1)
            session.add(row)
        row.kill_switch = kill_switch
        row.kill_switch_reason = reason
        row.updated_at = datetime.now(UTC)
        await session.commit()


async def load_bot_state() -> BotStateRow | None:
    async with AsyncSessionLocal() as session:
        return await session.get(BotStateRow, 1)


async def realized_pnl_since(start: datetime) -> float:
    """Sum of realized live PnL since `start` (UTC). Paper trades do not count."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.coalesce(func.sum(TradeRow.realized_profit_usd), 0.0)).where(
                TradeRow.ts >= start,
                TradeRow.mode == "live",
                TradeRow.realized_profit_usd.is_not(None),
            )
        )
        return float(result.scalar_one() or 0.0)
