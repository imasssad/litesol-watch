from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models import OpportunityRow, TradeRow
from app.models.schemas import Opportunity, TradeRecord


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
        rows = (await session.execute(select(OpportunityRow).order_by(OpportunityRow.id.desc()).limit(limit))).scalars().all()
        return [Opportunity.model_validate({c.name: getattr(r, c.name) for c in OpportunityRow.__table__.columns if c.name != "id"}) for r in rows]


async def recent_trades(limit: int = 50) -> list[TradeRecord]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(TradeRow).order_by(TradeRow.id.desc()).limit(limit))).scalars().all()
        return [TradeRecord.model_validate({c.name: getattr(r, c.name) for c in TradeRow.__table__.columns if c.name != "id"}) for r in rows]
