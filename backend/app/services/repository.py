from datetime import datetime, UTC

from sqlalchemy import select

from app.db.models import OpportunityRow, TradeRow
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
