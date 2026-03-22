"""
Data maintenance for Agent Economy.

Handles downsampling of historical market data and taking economy snapshots.
Called by the maintenance_cli.py every 6 hours.

Operations:
1. Aggregate raw MarketTrades older than 24h into hourly PriceAggregates (OHLCV)
2. Aggregate hourly PriceAggregates older than 30 days into daily aggregates
3. Delete raw MarketTrade records older than 48h (keep a buffer)
4. Delete old Transaction records older than 7 days (aggregates are preserved)
5. Take an EconomySnapshot of current macro stats
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from backend.clock import Clock

logger = logging.getLogger(__name__)


def calculate_gini(balances: list[float]) -> float:
    """
    Calculate the Gini coefficient from a list of balances.

    The Gini coefficient measures wealth inequality:
    - 0.0 = perfect equality (everyone has the same balance)
    - 1.0 = maximum inequality (one agent has everything)

    Uses the standard sorted-values formula:
        G = (2 * sum(i * x_i)) / (n * sum(x_i)) - (n + 1) / n
    where x_i are the sorted non-negative values and i is 1-indexed rank.

    Args:
        balances: List of agent balances (may include negatives, which are
                  clamped to 0 for the calculation).

    Returns:
        Gini coefficient in [0.0, 1.0], or 0.0 if fewer than 2 agents.
    """
    if len(balances) < 2:
        return 0.0

    # Clamp negatives to zero — wealth can't be negative for Gini purposes
    values = sorted(max(0.0, float(b)) for b in balances)
    n = len(values)
    total = sum(values)

    if total == 0.0:
        return 0.0

    weighted_sum = sum((i + 1) * v for i, v in enumerate(values))
    gini = (2.0 * weighted_sum) / (n * total) - (n + 1.0) / n
    return max(0.0, min(1.0, gini))


async def downsample_data(db: AsyncSession, clock: "Clock") -> dict:
    """
    Run all data maintenance operations.

    Args:
        db:    Active async database session (caller commits).
        clock: Clock for "now" reference.

    Returns:
        Summary dict with counts of records processed/deleted.
    """
    now = clock.now()
    results: dict = {
        "timestamp": now.isoformat(),
        "hourly_aggregates_created": 0,
        "daily_aggregates_created": 0,
        "raw_trades_deleted": 0,
        "old_transactions_deleted": 0,
        "snapshot_taken": False,
    }

    try:
        results["hourly_aggregates_created"] = await _aggregate_to_hourly(db, now)
    except Exception:
        logger.exception("Failed to create hourly price aggregates")

    try:
        results["daily_aggregates_created"] = await _aggregate_hourly_to_daily(db, now)
    except Exception:
        logger.exception("Failed to create daily price aggregates")

    try:
        results["raw_trades_deleted"] = await _delete_old_raw_trades(db, now)
    except Exception:
        logger.exception("Failed to delete old raw trades")

    try:
        results["old_transactions_deleted"] = await _delete_old_transactions(db, now)
    except Exception:
        logger.exception("Failed to delete old transactions")

    try:
        await _take_economy_snapshot(db, now)
        results["snapshot_taken"] = True
    except Exception:
        logger.exception("Failed to take economy snapshot")

    return results


async def _aggregate_to_hourly(db: AsyncSession, now: datetime) -> int:
    """
    Aggregate raw MarketTrade records older than 24h into hourly PriceAggregates.

    Groups trades by (good_slug, hour bucket) and computes OHLCV.
    Uses ON CONFLICT DO NOTHING to skip already-aggregated periods.

    Returns count of new aggregate records created.
    """
    from backend.models.aggregate import PriceAggregate
    from backend.models.marketplace import MarketTrade

    cutoff = now - timedelta(hours=24)

    # Raw SQL for the OHLCV aggregation (window functions in subquery)
    raw_sql = text("""
        WITH trade_data AS (
            SELECT
                good_slug,
                date_trunc('hour', executed_at) AS period_start,
                price,
                quantity,
                executed_at,
                ROW_NUMBER() OVER (
                    PARTITION BY good_slug, date_trunc('hour', executed_at)
                    ORDER BY executed_at ASC
                ) AS rn_first,
                ROW_NUMBER() OVER (
                    PARTITION BY good_slug, date_trunc('hour', executed_at)
                    ORDER BY executed_at DESC
                ) AS rn_last
            FROM market_trades
            WHERE executed_at < :cutoff
        ),
        ohlcv AS (
            SELECT
                good_slug,
                period_start,
                MAX(CASE WHEN rn_first = 1 THEN price END) AS open_price,
                MAX(price) AS high_price,
                MIN(price) AS low_price,
                MAX(CASE WHEN rn_last = 1 THEN price END) AS close_price,
                SUM(quantity) AS volume,
                SUM(price * quantity) AS total_value
            FROM trade_data
            GROUP BY good_slug, period_start
        )
        INSERT INTO price_aggregates
            (id, good_slug, period_type, period_start, open_price, high_price,
             low_price, close_price, volume, total_value)
        SELECT
            gen_random_uuid(),
            good_slug,
            'hourly',
            period_start,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            total_value
        FROM ohlcv
        ON CONFLICT (good_slug, period_type, period_start) DO NOTHING
        RETURNING id
    """)

    result = await db.execute(raw_sql, {"cutoff": cutoff})
    rows = result.fetchall()
    count = len(rows)
    logger.info("Created %d hourly price aggregates", count)
    return count


async def _aggregate_hourly_to_daily(db: AsyncSession, now: datetime) -> int:
    """
    Aggregate hourly PriceAggregates older than 30 days into daily aggregates.

    Groups hourly buckets by day and recomputes OHLCV across the day.
    Uses ON CONFLICT DO NOTHING to skip already-aggregated days.

    Returns count of new daily aggregate records created.
    """
    cutoff = now - timedelta(days=30)

    raw_sql = text("""
        WITH hourly_data AS (
            SELECT
                good_slug,
                date_trunc('day', period_start) AS day_start,
                period_start,
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
                total_value,
                ROW_NUMBER() OVER (
                    PARTITION BY good_slug, date_trunc('day', period_start)
                    ORDER BY period_start ASC
                ) AS rn_first,
                ROW_NUMBER() OVER (
                    PARTITION BY good_slug, date_trunc('day', period_start)
                    ORDER BY period_start DESC
                ) AS rn_last
            FROM price_aggregates
            WHERE period_type = 'hourly'
              AND period_start < :cutoff
        ),
        daily_ohlcv AS (
            SELECT
                good_slug,
                day_start AS period_start,
                MAX(CASE WHEN rn_first = 1 THEN open_price END) AS open_price,
                MAX(high_price) AS high_price,
                MIN(low_price) AS low_price,
                MAX(CASE WHEN rn_last = 1 THEN close_price END) AS close_price,
                SUM(volume) AS volume,
                SUM(total_value) AS total_value
            FROM hourly_data
            GROUP BY good_slug, day_start
        )
        INSERT INTO price_aggregates
            (id, good_slug, period_type, period_start, open_price, high_price,
             low_price, close_price, volume, total_value)
        SELECT
            gen_random_uuid(),
            good_slug,
            'daily',
            period_start,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            total_value
        FROM daily_ohlcv
        ON CONFLICT (good_slug, period_type, period_start) DO NOTHING
        RETURNING id
    """)

    result = await db.execute(raw_sql, {"cutoff": cutoff})
    rows = result.fetchall()
    count = len(rows)
    logger.info("Created %d daily price aggregates", count)
    return count


async def _delete_old_raw_trades(db: AsyncSession, now: datetime) -> int:
    """
    Delete raw MarketTrade records older than 48h.

    We keep a 48h buffer (vs the 24h aggregation threshold) to ensure
    aggregation has completed before we delete the source data.

    Returns count of records deleted.
    """
    from backend.models.marketplace import MarketTrade

    cutoff = now - timedelta(hours=48)
    stmt = delete(MarketTrade).where(MarketTrade.executed_at < cutoff)
    result = await db.execute(stmt)
    count = result.rowcount
    logger.info("Deleted %d raw market trades older than 48h", count)
    return count


async def _delete_old_transactions(db: AsyncSession, now: datetime) -> int:
    """
    Delete Transaction records older than 7 days.

    The raw transaction log grows continuously. After 7 days, the
    aggregated price data and economy snapshots provide the historical
    record. We retain recent transactions for the agent dashboard.

    Returns count of records deleted.
    """
    from backend.models.transaction import Transaction

    cutoff = now - timedelta(days=7)
    stmt = delete(Transaction).where(Transaction.created_at < cutoff)
    result = await db.execute(stmt)
    count = result.rowcount
    logger.info("Deleted %d transactions older than 7 days", count)
    return count


async def _take_economy_snapshot(db: AsyncSession, now: datetime) -> None:
    """
    Take a snapshot of current macro-level economy statistics.

    Computes:
    - Total money supply (sum of all agent balances + bank reserves)
    - Population (active agents)
    - Employment rate (employed / total)
    - Gini coefficient of agent wealth distribution
    - Active business counts (player vs NPC)
    - Current government type
    - Average bread price (proxy for cost of living)
    """
    from backend.models.agent import Agent
    from backend.models.aggregate import EconomySnapshot
    from backend.models.banking import CentralBank
    from backend.models.business import Business, Employment
    from backend.models.government import GovernmentState
    from backend.models.marketplace import MarketTrade

    # Population: agents not currently bankrupt or fully dead
    pop_result = await db.execute(select(func.count()).select_from(Agent))
    population = pop_result.scalar_one() or 0

    # Money supply: sum all agent wallet balances + central bank reserves.
    # When an agent deposits, their wallet goes down and bank reserves go up,
    # so reserves already include deposit balances. Do NOT add BankAccount
    # balances separately — that would double-count deposits.
    # Formula: money_supply = sum(agent.balance) + central_bank.reserves
    agent_balance_result = await db.execute(select(func.sum(Agent.balance)))
    agent_balance_total = float(agent_balance_result.scalar_one() or 0)

    cb_result = await db.execute(select(CentralBank))
    cb = cb_result.scalars().first()
    bank_reserves = float(cb.reserves) if cb else 0.0

    money_supply = agent_balance_total + bank_reserves

    # Employment rate
    total_agents_result = await db.execute(select(func.count()).select_from(Agent))
    total_agents = total_agents_result.scalar_one() or 1

    employed_result = await db.execute(
        select(func.count(func.distinct(Employment.agent_id))).where(
            Employment.terminated_at.is_(None)
        )
    )
    employed_count = employed_result.scalar_one() or 0
    employment_rate = employed_count / max(total_agents, 1)

    # Gini coefficient from all agent balances
    balance_result = await db.execute(select(Agent.balance))
    all_balances = [float(row[0]) for row in balance_result.fetchall()]
    gini = calculate_gini(all_balances) if all_balances else None

    # GDP proxy: recent transaction volume (last 6 hours)
    gdp_cutoff = now - timedelta(hours=6)
    from backend.models.transaction import Transaction

    gdp_result = await db.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.created_at >= gdp_cutoff,
            Transaction.type.in_(
                ["marketplace", "storefront", "wage", "gathering"]
            ),
        )
    )
    gdp = float(gdp_result.scalar_one() or 0)

    # Active businesses
    active_biz_result = await db.execute(
        select(func.count()).select_from(Business).where(
            Business.closed_at.is_(None)
        )
    )
    active_businesses = active_biz_result.scalar_one() or 0

    npc_biz_result = await db.execute(
        select(func.count()).select_from(Business).where(
            Business.closed_at.is_(None),
            Business.is_npc.is_(True),
        )
    )
    npc_businesses = npc_biz_result.scalar_one() or 0

    # Current government type
    gov_result = await db.execute(select(GovernmentState))
    gov = gov_result.scalars().first()
    government_type = gov.current_template_slug if gov else "unknown"

    # Average bread price: use recent trades
    bread_cutoff = now - timedelta(hours=6)
    bread_result = await db.execute(
        select(func.avg(MarketTrade.price)).where(
            MarketTrade.good_slug == "bread",
            MarketTrade.executed_at >= bread_cutoff,
        )
    )
    avg_bread_price_raw = bread_result.scalar_one()
    avg_bread_price = Decimal(str(avg_bread_price_raw)).quantize(Decimal("0.01")) if avg_bread_price_raw else None

    snapshot = EconomySnapshot(
        timestamp=now,
        gdp=Decimal(str(gdp)).quantize(Decimal("0.01")),
        money_supply=Decimal(str(money_supply)).quantize(Decimal("0.01")),
        population=population,
        employment_rate=employment_rate,
        gini_coefficient=gini,
        active_businesses=active_businesses,
        npc_businesses=npc_businesses,
        government_type=government_type,
        avg_bread_price=avg_bread_price,
    )
    db.add(snapshot)
    await db.flush()

    logger.info(
        "Economy snapshot: gdp=%.2f money=%.2f pop=%d emp=%.1f%% gini=%.3f",
        gdp,
        money_supply,
        population,
        employment_rate * 100,
        gini or 0,
    )
