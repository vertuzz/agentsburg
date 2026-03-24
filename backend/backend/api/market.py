"""
API endpoints: market order book and leaderboards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, desc, func, select

from backend.database import get_db
from backend.models.agent import Agent
from backend.models.banking import BankAccount
from backend.models.business import Business, Employment
from backend.models.good import Good
from backend.models.marketplace import MarketOrder, MarketTrade
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["api"])


@router.get("/market/{good}")
async def get_market(
    good: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Market info for a specific good.

    Returns order book depth, price history, and 24h stats.
    """
    now = datetime.now(UTC)
    one_day_ago = now - timedelta(hours=24)

    # Verify the good exists
    good_result = await db.execute(select(Good).where(Good.slug == good))
    good_obj = good_result.scalar_one_or_none()
    if good_obj is None:
        raise HTTPException(status_code=404, detail=f"Good {good!r} not found")

    # --- Open buy orders (aggregated by price level) ---
    buy_result = await db.execute(
        select(
            MarketOrder.price,
            func.sum(MarketOrder.quantity_total - MarketOrder.quantity_filled).label("total_qty"),
            func.count(MarketOrder.id).label("order_count"),
        )
        .where(
            and_(
                MarketOrder.good_slug == good,
                MarketOrder.side == "buy",
                MarketOrder.status.in_(["open", "partially_filled"]),
            )
        )
        .group_by(MarketOrder.price)
        .order_by(desc(MarketOrder.price))
        .limit(20)
    )
    buy_rows = buy_result.all()

    buy_orders = [
        {
            "price": float(row.price),
            "quantity": int(row.total_qty),
            "order_count": int(row.order_count),
        }
        for row in buy_rows
    ]

    # --- Open sell orders (aggregated by price level) ---
    sell_result = await db.execute(
        select(
            MarketOrder.price,
            func.sum(MarketOrder.quantity_total - MarketOrder.quantity_filled).label("total_qty"),
            func.count(MarketOrder.id).label("order_count"),
        )
        .where(
            and_(
                MarketOrder.good_slug == good,
                MarketOrder.side == "sell",
                MarketOrder.status.in_(["open", "partially_filled"]),
            )
        )
        .group_by(MarketOrder.price)
        .order_by(MarketOrder.price)
        .limit(20)
    )
    sell_rows = sell_result.all()

    sell_orders = [
        {
            "price": float(row.price),
            "quantity": int(row.total_qty),
            "order_count": int(row.order_count),
        }
        for row in sell_rows
    ]

    # Best prices
    best_buy = buy_orders[0]["price"] if buy_orders else None
    best_sell = sell_orders[0]["price"] if sell_orders else None

    # --- Price history (last 100 trades) ---
    history_result = await db.execute(
        select(MarketTrade).where(MarketTrade.good_slug == good).order_by(desc(MarketTrade.executed_at)).limit(100)
    )
    recent_trades = history_result.scalars().all()

    price_history = [
        {
            "price": float(t.price),
            "quantity": t.quantity,
            "executed_at": t.executed_at.isoformat(),
        }
        for t in reversed(recent_trades)  # oldest first for charting
    ]

    # --- 24h stats ---
    stats_result = await db.execute(
        select(
            func.coalesce(func.sum(MarketTrade.quantity * MarketTrade.price), 0).label("volume_value"),
            func.coalesce(func.sum(MarketTrade.quantity), 0).label("volume_qty"),
            func.max(MarketTrade.price).label("high"),
            func.min(MarketTrade.price).label("low"),
            func.avg(MarketTrade.price).label("average"),
        ).where(
            and_(
                MarketTrade.good_slug == good,
                MarketTrade.executed_at >= one_day_ago,
            )
        )
    )
    stats_row = stats_result.one()

    return {
        "good": good_obj.to_dict(),
        "order_book": {
            "buy": buy_orders,
            "sell": sell_orders,
            "best_buy": best_buy,
            "best_sell": best_sell,
        },
        "price_history": price_history,
        "stats_24h": {
            "volume_value": float(stats_row.volume_value or 0),
            "volume_qty": int(stats_row.volume_qty or 0),
            "high": float(stats_row.high) if stats_row.high else None,
            "low": float(stats_row.low) if stats_row.low else None,
            "average": float(stats_row.average) if stats_row.average else None,
        },
    }


@router.get("/leaderboards")
async def get_leaderboards(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Multiple leaderboard rankings.

    Returns richest agents, most revenue, biggest employers,
    longest surviving, and most productive agents.
    """
    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)
    limit = 20

    # --- Richest: balance + bank deposits ---
    agents_result = await db.execute(select(Agent).order_by(desc(Agent.balance)).limit(100))
    all_agents = agents_result.scalars().all()

    # Get bank accounts for all these agents
    if all_agents:
        agent_ids = [a.id for a in all_agents]
        accounts_result = await db.execute(select(BankAccount).where(BankAccount.agent_id.in_(agent_ids)))
        accounts = {acc.agent_id: float(acc.balance) for acc in accounts_result.scalars().all()}
    else:
        accounts = {}

    richest = []
    agent_wealth = []
    for agent in all_agents:
        total_wealth = float(agent.balance) + accounts.get(agent.id, 0.0)
        agent_wealth.append((agent, total_wealth))

    agent_wealth.sort(key=lambda x: x[1], reverse=True)
    for rank, (agent, wealth) in enumerate(agent_wealth[:limit], 1):
        richest.append(
            {
                "rank": rank,
                "agent_name": agent.name,
                "agent_model": agent.model,
                "value": round(wealth, 2),
                "wallet": round(float(agent.balance), 2),
                "bank": round(accounts.get(agent.id, 0.0), 2),
            }
        )

    # --- Most revenue: sum of incoming marketplace+storefront txns, last 7d ---
    revenue_result = await db.execute(
        select(
            Transaction.to_agent_id,
            func.sum(Transaction.amount).label("total_revenue"),
        )
        .where(
            and_(
                Transaction.type.in_(["marketplace", "storefront"]),
                Transaction.to_agent_id.isnot(None),
                Transaction.created_at >= seven_days_ago,
            )
        )
        .group_by(Transaction.to_agent_id)
        .order_by(desc("total_revenue"))
        .limit(limit)
    )
    revenue_rows = revenue_result.all()

    most_revenue = []
    if revenue_rows:
        rev_agent_ids = [row.to_agent_id for row in revenue_rows]
        rev_agents_result = await db.execute(select(Agent).where(Agent.id.in_(rev_agent_ids)))
        rev_agents = {a.id: a for a in rev_agents_result.scalars().all()}
        for rank, row in enumerate(revenue_rows, 1):
            agent = rev_agents.get(row.to_agent_id)
            most_revenue.append(
                {
                    "rank": rank,
                    "agent_name": agent.name if agent else "Unknown",
                    "agent_model": agent.model if agent else None,
                    "value": round(float(row.total_revenue), 2),
                }
            )

    # --- Biggest employers: most active employees ---
    employer_result = await db.execute(
        select(
            Business.owner_id,
            func.count(Employment.id).label("employee_count"),
        )
        .join(Employment, Employment.business_id == Business.id)
        .where(
            and_(
                Employment.terminated_at.is_(None),
                Business.closed_at.is_(None),
            )
        )
        .group_by(Business.owner_id)
        .order_by(desc("employee_count"))
        .limit(limit)
    )
    employer_rows = employer_result.all()

    biggest_employers = []
    if employer_rows:
        emp_agent_ids = [row.owner_id for row in employer_rows]
        emp_agents_result = await db.execute(select(Agent).where(Agent.id.in_(emp_agent_ids)))
        emp_agents = {a.id: a for a in emp_agents_result.scalars().all()}
        for rank, row in enumerate(employer_rows, 1):
            agent = emp_agents.get(row.owner_id)
            biggest_employers.append(
                {
                    "rank": rank,
                    "agent_name": agent.name if agent else "Unknown",
                    "agent_model": agent.model if agent else None,
                    "value": int(row.employee_count),
                }
            )

    # --- Longest surviving: oldest agents by created_at with no bankruptcy ---
    # Sort by age, prefer zero bankruptcies first
    survivor_result = await db.execute(
        select(Agent).order_by(Agent.bankruptcy_count.asc(), Agent.created_at.asc()).limit(limit)
    )
    survivors = survivor_result.scalars().all()

    longest_surviving = []
    for rank, agent in enumerate(survivors, 1):
        age_days = (now - agent.created_at).total_seconds() / 86400
        longest_surviving.append(
            {
                "rank": rank,
                "agent_name": agent.name,
                "agent_model": agent.model,
                "value": round(age_days, 2),
                "unit": "days",
                "bankruptcy_count": agent.bankruptcy_count,
                "is_active": agent.is_active,
            }
        )

    # --- Most productive: most work() transactions in last 7d ---
    productive_result = await db.execute(
        select(
            Transaction.to_agent_id,
            func.count(Transaction.id).label("work_count"),
        )
        .where(
            and_(
                Transaction.type == "wage",
                Transaction.to_agent_id.isnot(None),
                Transaction.created_at >= seven_days_ago,
            )
        )
        .group_by(Transaction.to_agent_id)
        .order_by(desc("work_count"))
        .limit(limit)
    )
    productive_rows = productive_result.all()

    most_productive = []
    if productive_rows:
        prod_agent_ids = [row.to_agent_id for row in productive_rows]
        prod_agents_result = await db.execute(select(Agent).where(Agent.id.in_(prod_agent_ids)))
        prod_agents = {a.id: a for a in prod_agents_result.scalars().all()}
        for rank, row in enumerate(productive_rows, 1):
            agent = prod_agents.get(row.to_agent_id)
            most_productive.append(
                {
                    "rank": rank,
                    "agent_name": agent.name if agent else "Unknown",
                    "agent_model": agent.model if agent else None,
                    "value": int(row.work_count),
                    "unit": "work calls",
                }
            )

    return {
        "richest": richest,
        "most_revenue": most_revenue,
        "biggest_employers": biggest_employers,
        "longest_surviving": longest_surviving,
        "most_productive": most_productive,
    }
