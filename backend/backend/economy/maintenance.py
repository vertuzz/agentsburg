"""
Data maintenance for Agent Economy.

Handles downsampling of historical market data and orchestrates economy
snapshots. Called by the maintenance_cli.py every 6 hours.

Operations:
1. Aggregate raw MarketTrades older than 24h into hourly PriceAggregates (OHLCV)
2. Aggregate hourly PriceAggregates older than 30 days into daily aggregates
3. Delete raw MarketTrade records older than 48h (keep a buffer)
4. Delete old Transaction records older than 7 days (aggregates are preserved)
5. Take an EconomySnapshot of current macro stats
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

# Re-export for backward compatibility
from backend.economy.snapshots import calculate_gini, take_economy_snapshot

if TYPE_CHECKING:
    from backend.clock import Clock

logger = logging.getLogger(__name__)

# Make re-exports visible to "from maintenance import *" and static analysis
__all__ = [
    "calculate_gini",
    "downsample_data",
    "take_economy_snapshot",
]


async def downsample_data(db: AsyncSession, clock: Clock) -> dict:
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
        await take_economy_snapshot(db, now)
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
    cutoff = now - timedelta(hours=24)

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
