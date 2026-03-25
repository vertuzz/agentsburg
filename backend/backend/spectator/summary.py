"""
Daily summary for Agent Economy spectator experience.

Aggregates top events, market movers, and key stats into a single
digestible summary. All data is derived from existing models and Redis.

Redis keys:
    spectator:daily_summary           — cached summary (1h TTL)
    spectator:wealth_snapshot:{date}  — per-model wealth snapshot (7d TTL)
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select

from backend.models.agent import Agent
from backend.models.aggregate import PriceAggregate
from backend.models.banking import BankAccount
from backend.models.transaction import Transaction
from backend.spectator.events import get_spectator_feed

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock

logger = logging.getLogger(__name__)

SUMMARY_CACHE_KEY = "spectator:daily_summary"
SUMMARY_CACHE_TTL = 3600  # 1 hour

WEALTH_SNAPSHOT_TTL = 604800  # 7 days


async def generate_daily_summary(
    db: AsyncSession,
    redis: aioredis.Redis,
    clock: Clock,
) -> dict:
    """
    Generate a daily summary with top events, market movers, and stats.

    Checks Redis cache first; computes from DB on miss.
    """
    cached = await redis.get(SUMMARY_CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, TypeError):
            pass

    result = await _compute_daily_summary(db, redis, clock)

    try:
        await redis.set(SUMMARY_CACHE_KEY, json.dumps(result), ex=SUMMARY_CACHE_TTL)
    except Exception:
        logger.warning("Failed to cache daily summary", exc_info=True)

    return result


async def _compute_daily_summary(
    db: AsyncSession,
    redis: aioredis.Redis,
    clock: Clock,
) -> dict:
    """Build the daily summary from DB and Redis data."""
    now = clock.now()
    one_day_ago = now - timedelta(hours=24)

    # --- Top events: notable/critical from spectator feed ---
    top_events = await get_spectator_feed(redis, limit=5, min_drama="notable")

    # --- Market movers: goods with biggest price changes in last 24h ---
    market_movers = await _get_market_movers(db, one_day_ago)

    # --- Stats ---
    stats = await _get_daily_stats(db, one_day_ago)

    return {
        "top_events": top_events,
        "market_movers": market_movers,
        "stats": stats,
        "generated_at": now.isoformat(),
    }


async def _get_market_movers(db: AsyncSession, since: object) -> list[dict]:
    """Find goods with the biggest price changes in the last 24h."""
    # Get hourly aggregates from last 24h
    stmt = (
        select(PriceAggregate)
        .where(
            and_(
                PriceAggregate.period_type == "hourly",
                PriceAggregate.period_start >= since,
            )
        )
        .order_by(PriceAggregate.period_start)
    )
    result = await db.execute(stmt)
    aggregates = result.scalars().all()

    if not aggregates:
        return []

    # Group by good_slug, find earliest open and latest close
    goods: dict[str, dict] = {}
    for agg in aggregates:
        slug = agg.good_slug
        if slug not in goods:
            goods[slug] = {
                "good_slug": slug,
                "earliest_open": float(agg.open_price),
                "latest_close": float(agg.close_price),
                "earliest_start": agg.period_start,
                "latest_start": agg.period_start,
            }
        else:
            entry = goods[slug]
            if agg.period_start < entry["earliest_start"]:
                entry["earliest_open"] = float(agg.open_price)
                entry["earliest_start"] = agg.period_start
            if agg.period_start > entry["latest_start"]:
                entry["latest_close"] = float(agg.close_price)
                entry["latest_start"] = agg.period_start

    # Compute price change and sort by absolute change
    movers = []
    for slug, data in goods.items():
        change = data["latest_close"] - data["earliest_open"]
        pct_change = (change / data["earliest_open"] * 100) if data["earliest_open"] != 0 else 0.0
        movers.append(
            {
                "good_slug": slug,
                "open_price": data["earliest_open"],
                "close_price": data["latest_close"],
                "change": round(change, 2),
                "pct_change": round(pct_change, 1),
            }
        )

    movers.sort(key=lambda m: abs(m["change"]), reverse=True)
    return movers[:3]


async def _get_daily_stats(db: AsyncSession, since: object) -> dict:
    """Compute population, GDP, and bankruptcies for the last 24h."""
    # Population
    pop_result = await db.execute(select(func.count(Agent.id)))
    population = pop_result.scalar() or 0

    # GDP: marketplace + storefront transaction volume in last 24h
    gdp_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            and_(
                Transaction.type.in_(["marketplace", "storefront"]),
                Transaction.created_at >= since,
            )
        )
    )
    gdp_24h = float(gdp_result.scalar() or 0)

    # Bankruptcies in last 24h
    bankrupt_result = await db.execute(
        select(func.count(Transaction.id)).where(
            and_(
                Transaction.type == "bankruptcy_liquidation",
                Transaction.created_at >= since,
            )
        )
    )
    bankruptcies_24h = bankrupt_result.scalar() or 0

    return {
        "population": population,
        "gdp_24h": gdp_24h,
        "bankruptcies_24h": bankruptcies_24h,
    }


async def snapshot_wealth_data(
    db: AsyncSession,
    redis: aioredis.Redis,
    clock: Clock,
) -> None:
    """
    Store per-model wealth snapshot for trend comparison.

    Called from the daily tick. Stores a JSON object keyed by model name
    with total wealth for each model group.
    """
    now = clock.now()
    date_str = now.strftime("%Y-%m-%d")
    key = f"spectator:wealth_snapshot:{date_str}"

    stmt = (
        select(
            Agent.model,
            func.sum(Agent.balance + func.coalesce(BankAccount.balance, 0)).label("total_wealth"),
            func.count(Agent.id).label("agent_count"),
        )
        .outerjoin(BankAccount, BankAccount.agent_id == Agent.id)
        .where(Agent.model.isnot(None))
        .group_by(Agent.model)
    )
    result = await db.execute(stmt)
    rows = result.all()

    snapshot = {}
    for row in rows:
        snapshot[row.model] = {
            "total_wealth": float(row.total_wealth or 0),
            "agent_count": int(row.agent_count or 0),
        }

    await redis.set(key, json.dumps(snapshot), ex=WEALTH_SNAPSHOT_TTL)
    logger.info("Stored wealth snapshot for %s (%d models)", date_str, len(snapshot))
