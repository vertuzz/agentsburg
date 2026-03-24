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
import random
from typing import TYPE_CHECKING

from backend.economy.bankruptcy import process_bankruptcies
from backend.economy.fast_tick import run_fast_tick
from backend.economy.slow_tick import enforce_reserve_floor, process_rent, process_survival_costs

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

TICK_LOCK_KEY = "tick:lock"
TICK_LOCK_TTL = 120  # seconds

LAST_HOURLY_KEY = "tick:last_hourly"
LAST_DAILY_KEY = "tick:last_daily"
LAST_WEEKLY_KEY = "tick:last_weekly"

HOURLY_INTERVAL = 3600  # 1 hour in seconds
DAILY_INTERVAL = 86400  # 24 hours in seconds
WEEKLY_INTERVAL = 604800  # 7 days in seconds


async def run_tick(
    db: AsyncSession,
    redis: aioredis.Redis,
    clock: Clock,
    settings: Settings,
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
        fast_result = await run_fast_tick(db, clock, settings, redis=redis)
        results["fast_tick"] = fast_result

        # --- Check hourly boundary ---
        # Compute how many hours have elapsed since the last slow tick.
        # In production (called every ~60s) this is always 1.
        # In tests with large time jumps this can be 24+ hours, and we
        # pass the count to slow tick functions so they multiply costs
        # accordingly — much faster than looping 24 times.
        last_hourly_str = await redis.get(LAST_HOURLY_KEY)
        if last_hourly_str:
            last_hourly = float(last_hourly_str)
        else:
            # First tick ever — initialize to "just over 1 hour ago" so the
            # first tick fires immediately (accounting for jitter).
            last_hourly = now_ts - HOURLY_INTERVAL - 61

        if now_ts - last_hourly >= HOURLY_INTERVAL + random.uniform(0, 60):
            elapsed_hours = max(1, int((now_ts - last_hourly) / HOURLY_INTERVAL))
            # Cap at 168 hours (1 week) to prevent runaway catch-up
            elapsed_hours = min(elapsed_hours, 168)
            logger.info(
                "Running slow tick at %s (catching up %d hours)",
                now.isoformat(),
                elapsed_hours,
            )
            slow_results = await _run_slow_tick(db, clock, settings, hours=elapsed_hours, redis=redis)
            slow_results["_hours"] = elapsed_hours
            results["slow_tick"] = slow_results
            await redis.set(LAST_HOURLY_KEY, str(now_ts))

        # --- Check daily boundary ---
        last_daily_str = await redis.get(LAST_DAILY_KEY)
        last_daily = float(last_daily_str) if last_daily_str else now_ts - DAILY_INTERVAL - 61

        if now_ts - last_daily >= DAILY_INTERVAL + random.uniform(0, 60):
            logger.info("Running daily tick at %s", now.isoformat())
            daily_results = await _run_daily_tick(db, clock, settings, redis=redis)
            results["daily_tick"] = daily_results
            await redis.set(LAST_DAILY_KEY, str(now_ts))

        # --- Check weekly boundary (election tally) ---
        last_weekly_str = await redis.get(LAST_WEEKLY_KEY)
        last_weekly = float(last_weekly_str) if last_weekly_str else now_ts - WEEKLY_INTERVAL - 61

        if now_ts - last_weekly >= WEEKLY_INTERVAL + random.uniform(0, 60):
            logger.info("Running weekly election tally at %s", now.isoformat())
            weekly_results = await _run_weekly_tick(db, clock, settings)
            results["weekly_tick"] = weekly_results
            await redis.set(LAST_WEEKLY_KEY, str(now_ts))

            # Spectator feed: election results
            await _emit_election_spectator_event(redis, clock, settings, weekly_results)

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
    clock: Clock,
    settings: Settings,
    hours: int = 1,
    redis: aioredis.Redis | None = None,
) -> dict:
    """
    Run all hourly slow tick processing.

    Args:
        hours: Number of hours to process in this tick (default 1).
               Values > 1 are used when catching up after a time jump.
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

    survival = await process_survival_costs(db, clock, settings, hours=hours, redis=redis)
    rent = await process_rent(db, clock, settings, hours=hours, redis=redis)

    # Phase 7: NPC business simulation (auto-produce, close unprofitable, spawn new)
    npc_biz_results = {"type": "npc_businesses", "skipped": True}
    try:
        from backend.economy.npc_businesses import simulate_npc_businesses

        npc_biz_results = await simulate_npc_businesses(db, clock, settings)
    except Exception:
        logger.exception("NPC business simulation failed — continuing")

    bankruptcy = await process_bankruptcies(db, clock, settings)

    # Ensure bank reserves stay above the configured floor.
    # Runs AFTER all organic replenishment (rent, taxes, loan payments)
    # so the injection is minimised.
    reserve_floor = await enforce_reserve_floor(db, clock, settings)

    # Flush to ensure consistency before bankruptcy check
    await db.flush()

    # --- Spectator feed: emit narrative events for notable tick results ---
    if redis is not None:
        await _emit_slow_tick_spectator_events(
            redis,
            clock,
            survival,
            rent,
            tax_results,
            audit_results,
            loan_payments,
            bankruptcy,
        )

    return {
        "tax_collection": tax_results,
        "audits": audit_results,
        "loan_payments": loan_payments,
        "deposit_interest": deposit_interest,
        "survival_costs": survival,
        "rent": rent,
        "npc_businesses": npc_biz_results,
        "bankruptcy": bankruptcy,
        "reserve_floor": reserve_floor,
    }


async def _run_daily_tick(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
    redis: aioredis.Redis | None = None,
) -> dict:
    """
    Run all daily tick processing.

    - Take an economy snapshot for macro tracking
    - Data maintenance (price history downsampling, old record cleanup)
    """
    processed = []

    # Economy snapshot for macro stats tracking
    snapshot_result = {"type": "economy_snapshot", "skipped": True}
    try:
        from backend.economy.maintenance import downsample_data

        snapshot_result = await downsample_data(db, clock)
        snapshot_result["type"] = "maintenance"
        processed.append("economy_snapshot")
        processed.append("data_maintenance")
    except Exception:
        logger.exception("Daily maintenance (snapshot/downsampling) failed — continuing")

    # Spectator: snapshot wealth data for daily summary trends
    if redis is not None:
        try:
            from backend.spectator.summary import snapshot_wealth_data

            await snapshot_wealth_data(db, redis, clock)
        except Exception:
            logger.warning("Failed to snapshot wealth data", exc_info=True)

    return {"processed": processed, "maintenance": snapshot_result}


async def _run_weekly_tick(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
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


async def _emit_slow_tick_spectator_events(
    redis: aioredis.Redis,
    clock: Clock,
    survival: dict,
    rent: dict,
    tax_results: dict,
    audit_results: dict,
    loan_payments: dict,
    bankruptcy: dict,
) -> None:
    """Emit spectator feed events for slow tick results."""
    from backend.spectator.events import emit_spectator_event

    try:
        # Survival costs (routine — always happens)
        if survival.get("agents_charged", 0) > 0:
            await emit_spectator_event(redis, "survival_costs", survival, clock, "routine")

        # Rent (notable if evictions happened)
        if rent.get("agents_charged", 0) > 0 or rent.get("agents_evicted", 0) > 0:
            drama = "notable" if rent.get("agents_evicted", 0) > 0 else "routine"
            await emit_spectator_event(redis, "rent_summary", rent, clock, drama)

        # Tax collection (routine)
        if not tax_results.get("skipped") and tax_results.get("total_collected", 0) > 0:
            await emit_spectator_event(redis, "tax_summary", tax_results, clock, "routine")

        # Audits (notable if violations found)
        if not audit_results.get("skipped"):
            drama = "notable" if audit_results.get("violations_found", 0) > 0 else "routine"
            if audit_results.get("agents_audited", 0) > 0:
                await emit_spectator_event(redis, "audit_summary", audit_results, clock, drama)

        # Loan defaults (notable)
        if not loan_payments.get("skipped") and loan_payments.get("defaults", 0) > 0:
            await emit_spectator_event(redis, "loan_default", loan_payments, clock, "notable")

        # Bankruptcies (critical!)
        if bankruptcy.get("count", 0) > 0:
            await emit_spectator_event(redis, "bankruptcy_summary", bankruptcy, clock, "critical")
    except Exception:
        logger.warning("Failed to emit slow tick spectator events", exc_info=True)


async def _emit_election_spectator_event(
    redis: aioredis.Redis,
    clock: Clock,
    settings: Settings,
    election_result: dict,
) -> None:
    """Emit a spectator event for election results."""
    from backend.spectator.events import emit_spectator_event

    if election_result.get("error"):
        return

    try:
        total_votes = election_result.get("total_votes", 0)
        vote_counts = election_result.get("vote_counts", {})
        winner = election_result.get("winner", "")
        winner_votes = vote_counts.get(winner, 0)
        vote_pct = round(winner_votes / total_votes * 100) if total_votes > 0 else 0

        # Look up template names
        templates = settings.government.get("templates", [])
        name_map = {t["slug"]: t.get("name", t["slug"]) for t in templates}

        detail = {
            "winner": winner,
            "winner_name": name_map.get(winner, winner),
            "previous": election_result.get("previous", ""),
            "previous_name": name_map.get(election_result.get("previous", ""), ""),
            "changed": election_result.get("changed", False),
            "total_votes": total_votes,
            "vote_pct": vote_pct,
            "vote_counts": vote_counts,
        }

        drama = "critical" if election_result.get("changed") else "notable"
        await emit_spectator_event(redis, "election", detail, clock, drama)
    except Exception:
        logger.warning("Failed to emit election spectator event", exc_info=True)
