"""
Main tick entry point for Agent Economy.

The tick is the heartbeat of the economy. It is called by a cron job every
minute (via economy/cli.py) and orchestrates all periodic processing.

Architecture:
- Uses a Redis lock (SETNX tick:lock) to prevent overlapping runs
- Always runs the fast tick (every invocation)
- Checks hourly/daily/weekly boundaries and runs appropriate ticks
- Tracks last tick times in Redis

Redis keys used:
    tick:lock          — SETNX lock, TTL=120s, prevents double-runs
    tick:last_hourly   — Unix timestamp of last hourly slow tick
    tick:last_daily    — Unix timestamp of last daily tick
    tick:last_weekly   — Unix timestamp of last weekly tick (election tally)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.economy.bankruptcy import process_bankruptcies
from backend.economy.fast_tick import run_fast_tick
from backend.economy.slow_tick import process_rent, process_survival_costs

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

TICK_LOCK_KEY = "tick:lock"
TICK_LOCK_TTL = 120  # seconds

LAST_HOURLY_KEY = "tick:last_hourly"
LAST_DAILY_KEY = "tick:last_daily"
LAST_WEEKLY_KEY = "tick:last_weekly"

HOURLY_INTERVAL = 3600    # 1 hour in seconds
DAILY_INTERVAL = 86400    # 24 hours in seconds
WEEKLY_INTERVAL = 604800  # 7 days in seconds


async def run_tick(
    db: AsyncSession,
    redis: aioredis.Redis,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Run one tick cycle.

    Acquires the Redis lock, runs all applicable tick phases, then
    releases the lock. If the lock is already held (another tick is
    running), returns immediately without processing.

    Args:
        db:       Active async database session.
        redis:    Redis client for locking and boundary tracking.
        clock:    Clock for time-dependent boundary checks.
        settings: Application settings.

    Returns:
        Dict summarizing what was processed this tick.
    """
    now = clock.now()
    now_ts = now.timestamp()

    # --- Acquire lock ---
    acquired = await redis.set(TICK_LOCK_KEY, "1", nx=True, ex=TICK_LOCK_TTL)
    if not acquired:
        logger.debug("Tick lock held by another process — skipping")
        return {"skipped": True, "reason": "lock_held"}

    results = {
        "timestamp": now.isoformat(),
        "fast_tick": None,
        "slow_tick": None,
        "daily_tick": None,
        "weekly_tick": None,
    }

    try:
        # --- Always run fast tick ---
        fast_result = await run_fast_tick(db, clock, settings)
        results["fast_tick"] = fast_result

        # --- Check hourly boundary ---
        last_hourly_str = await redis.get(LAST_HOURLY_KEY)
        last_hourly = float(last_hourly_str) if last_hourly_str else 0.0

        if now_ts - last_hourly >= HOURLY_INTERVAL:
            logger.info("Running hourly slow tick at %s", now.isoformat())
            slow_results = await _run_slow_tick(db, clock, settings)
            results["slow_tick"] = slow_results
            # Update boundary timestamp
            await redis.set(LAST_HOURLY_KEY, str(now_ts))

        # --- Check daily boundary ---
        last_daily_str = await redis.get(LAST_DAILY_KEY)
        last_daily = float(last_daily_str) if last_daily_str else 0.0

        if now_ts - last_daily >= DAILY_INTERVAL:
            logger.info("Running daily tick at %s", now.isoformat())
            daily_results = await _run_daily_tick(db, clock, settings)
            results["daily_tick"] = daily_results
            await redis.set(LAST_DAILY_KEY, str(now_ts))

        # --- Check weekly boundary (election tally) ---
        last_weekly_str = await redis.get(LAST_WEEKLY_KEY)
        last_weekly = float(last_weekly_str) if last_weekly_str else 0.0

        if now_ts - last_weekly >= WEEKLY_INTERVAL:
            logger.info("Running weekly election tally at %s", now.isoformat())
            weekly_results = await _run_weekly_tick(db, clock, settings)
            results["weekly_tick"] = weekly_results
            await redis.set(LAST_WEEKLY_KEY, str(now_ts))

        # Commit all changes from this tick cycle
        await db.commit()

    except Exception:
        await db.rollback()
        logger.exception("Tick processing failed — rolled back")
        raise
    finally:
        # Always release the lock
        await redis.delete(TICK_LOCK_KEY)

    return results


async def _run_slow_tick(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Run all hourly slow tick processing.

    Order matters:
    1. Tax collection (before survival deductions — we tax income first)
    2. Run audits (after taxes — so audit compares against just-filed records)
    3. Loan payments (financial obligations)
    4. Deposit interest (interest accrual)
    5. Survival costs (everyone pays food)
    6. Rent (housed agents pay; unaffordable = eviction)
    7. Bankruptcy check (agents too deep in debt are liquidated)
    """
    # Phase 6: Tax collection
    tax_results = {"type": "tax_collection", "skipped": True}
    audit_results = {"type": "audits", "skipped": True}
    try:
        from backend.government.taxes import collect_taxes, run_audits
        tax_results = await collect_taxes(db, clock, settings)
        audit_results = await run_audits(db, clock, settings)
    except Exception:
        logger.exception("Tax/audit processing failed — continuing")

    # Phase 5: Loan installments collected
    loan_payments = {"type": "loan_payments", "skipped": True}
    deposit_interest = {"type": "deposit_interest", "skipped": True}
    try:
        from backend.banking.service import process_deposit_interest, process_loan_payments
        loan_payments = await process_loan_payments(db, clock, settings)
        deposit_interest = await process_deposit_interest(db, clock, settings)
    except Exception:
        logger.exception("Banking tick processing failed — continuing")

    survival = await process_survival_costs(db, clock, settings)
    rent = await process_rent(db, clock, settings)

    # Phase 7: NPC business simulation (auto-produce, close unprofitable, spawn new)
    npc_biz_results = {"type": "npc_businesses", "skipped": True}
    try:
        from backend.economy.npc_businesses import simulate_npc_businesses
        npc_biz_results = await simulate_npc_businesses(db, clock, settings)
    except Exception:
        logger.exception("NPC business simulation failed — continuing")

    bankruptcy = await process_bankruptcies(db, clock, settings)

    # Flush to ensure consistency before bankruptcy check
    await db.flush()

    return {
        "tax_collection": tax_results,
        "audits": audit_results,
        "loan_payments": loan_payments,
        "deposit_interest": deposit_interest,
        "survival_costs": survival,
        "rent": rent,
        "npc_businesses": npc_biz_results,
        "bankruptcy": bankruptcy,
    }


async def _run_daily_tick(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Run all daily tick processing.

    Phase 7 will add:
    - NPC business adjustments
    - Price history downsampling
    """
    return {"processed": []}


async def _run_weekly_tick(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Run weekly tick: tally election and apply winning government template.

    This runs once per week. The election winner takes effect immediately.
    Existing loan rates are adjusted to reflect the new government's
    interest rate modifier.
    """
    try:
        from backend.government.service import tally_election
        result = await tally_election(db, clock, settings)
        logger.info(
            "Weekly election tally: winner=%s (changed=%s)",
            result.get("winner"),
            result.get("changed"),
        )
        return result
    except Exception:
        logger.exception("Election tally failed — continuing")
        return {"type": "election_tally", "error": "failed"}
