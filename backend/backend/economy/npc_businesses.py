"""
NPC Business Simulation for Agent Economy.

Runs during the slow tick (hourly). Handles all automated NPC business logic:

1. Drain excess inventory: If an NPC business has inventory above 70% of
   storage capacity, drain down to 50%. Prevents permanent "storage full".
   (Implementation: _drain_excess_inventory)

2. Auto-produce: NPC businesses produce goods each tick using their recipe
   (or directly add goods if no recipe). NPC efficiency = 0.5x.
   (Implementation in npc_production.py)

3. Profitability check: If an NPC business has been unprofitable for
   3+ consecutive periods, close it (it's been outcompeted by players).

4. Demand gap detection: If a good has high NPC demand but no supply,
   spawn a new NPC business to fill the gap.
   (Implementation in npc_production.py)

5. Price adjustment:
   - Inventory growing (not selling fast enough) → reduce prices 5-10%
   - Selling out every tick → increase prices 5-10%

NPC businesses each have an Agent account (named "NPC_Farm_01" etc.) that
receives revenue and pays costs. This lets the banking system track their
balance for closure decisions.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

# Re-export so existing imports from this module still work
from backend.economy.npc_production import (
    run_npc_production,
    spawn_demand_gap_businesses,
)
from backend.models.agent import Agent
from backend.models.banking import CentralBank
from backend.models.business import Business, JobPosting, StorefrontPrice
from backend.models.inventory import InventoryItem
from backend.models.recipe import Recipe
from backend.models.zone import Zone

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

# How many consecutive unprofitable periods before closing an NPC business
UNPROFITABLE_CLOSE_THRESHOLD = 3

# Storefront revenue stored in JSON metadata key for tracking
_REVENUE_HISTORY_KEY = "npc_revenue_history"

# Price adjustment magnitudes
PRICE_REDUCTION_FACTOR = 0.92  # 8% price cut when overstocked
PRICE_INCREASE_FACTOR = 1.08  # 8% price rise when selling out
MIN_PRICE = Decimal("0.50")  # Never price below 0.50

# Inventory drain thresholds (fraction of storage_capacity)
DRAIN_HIGH_WATERMARK = 0.7  # Start draining above this
DRAIN_LOW_WATERMARK = 0.5  # Drain down to this level
DRAIN_SALE_DISCOUNT = Decimal("0.5")  # NPC receives 50% of base_value for drained goods


async def simulate_npc_businesses(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
    redis: aioredis.Redis | None = None,
) -> dict:
    """
    Run all NPC business simulation logic for one slow tick.

    Steps:
      1. Auto-produce goods for each NPC business
      2. Check profitability → close chronic losers
      3. Check demand gaps → spawn new NPC businesses
      4. Adjust prices based on inventory levels
    """
    from backend.economy.npc_scaling import compute_npc_activity_factor, get_online_player_count

    now = clock.now()

    # Scale NPC production based on online player count
    online_count = await get_online_player_count(redis) if redis else 0
    activity_factor = compute_npc_activity_factor(online_count, settings)

    npc_efficiency = float(settings.economy.npc_worker_efficiency) * activity_factor
    npc_wage_mult = float(settings.economy.npc_worker_wage_multiplier)

    # Load all open NPC businesses
    result = await db.execute(
        select(Business).where(
            Business.is_npc == True,  # noqa: E712
            Business.closed_at.is_(None),
        )
    )
    npc_businesses = list(result.scalars().all())

    # Load agents (NPC owners)
    agents_result = await db.execute(select(Agent))
    agents_map: dict[str, Agent] = {str(a.id): a for a in agents_result.scalars().all()}

    # Load recipes
    recipes_result = await db.execute(select(Recipe))
    all_recipes = list(recipes_result.scalars().all())
    recipes_by_slug: dict[str, Recipe] = {r.slug: r for r in all_recipes}
    recipes_by_output: dict[str, list[Recipe]] = {}
    for recipe in all_recipes:
        recipes_by_output.setdefault(recipe.output_good, []).append(recipe)

    # Load bootstrap config for NPC businesses
    npc_biz_configs = settings.bootstrap.get("npc_businesses", [])
    npc_config_by_name: dict[str, dict] = {c["name"]: c for c in npc_biz_configs}

    # Load CentralBank
    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1))
    central_bank = bank_result.scalar_one_or_none()

    # --- Step 1: Drain excess inventory ---
    drain_log = await _drain_excess_inventory(db, npc_businesses, agents_map, central_bank, settings)

    # --- Step 2: Auto-produce ---
    production_log = await run_npc_production(
        db,
        npc_businesses,
        agents_map,
        recipes_by_slug,
        npc_config_by_name,
        central_bank,
        npc_efficiency,
        npc_wage_mult,
        settings,
    )

    # --- Step 3: Profitability check ---
    closures = _check_profitability(npc_businesses, agents_map, now)

    # --- Step 4: Demand gap spawning ---
    spawned = await spawn_demand_gap_businesses(
        db,
        settings,
        recipes_by_output,
        central_bank,
        now,
        activity_factor=activity_factor,
    )

    # --- Step 5: Price adjustment ---
    price_adjustments = await _adjust_prices(db, settings)

    # --- Step 6: Adjust NPC job wages based on player activity ---
    wage_adjustments = await _adjust_job_wages(db, npc_businesses, activity_factor, settings)

    await db.flush()

    logger.info(
        "NPC businesses: %d drained, %d produced, %d closed, %d spawned, %d price adj, %d wage adj (factor=%.2f)",
        len(drain_log),
        len(production_log),
        len(closures),
        len(spawned),
        len(price_adjustments),
        len(wage_adjustments),
        activity_factor,
    )

    return {
        "type": "npc_businesses",
        "timestamp": now.isoformat(),
        "activity_factor": activity_factor,
        "drain": drain_log,
        "production": production_log,
        "closures": closures,
        "spawned": spawned,
        "price_adjustments": price_adjustments,
        "wage_adjustments": wage_adjustments,
    }


async def _drain_excess_inventory(
    db: AsyncSession,
    npc_businesses: list[Business],
    agents_map: dict[str, Agent],
    central_bank: CentralBank | None,
    settings: Settings,
) -> list[dict]:
    """Step 1: Drain NPC inventory above high watermark down to low watermark.

    Prevents NPC businesses from getting permanently stuck at full storage,
    which blocks employee work() calls. Drained goods are consumed (removed
    from the economy). The NPC owner receives 50% of base_value as revenue.
    """
    goods_config = {g["slug"]: g for g in settings.goods}
    drain_log: list[dict] = []

    for biz in npc_businesses:
        high_mark = int(biz.storage_capacity * DRAIN_HIGH_WATERMARK)
        low_mark = int(biz.storage_capacity * DRAIN_LOW_WATERMARK)

        # Check total storage used
        used_result = await db.execute(
            select(func.coalesce(func.sum(InventoryItem.quantity), 0)).where(
                InventoryItem.owner_type == "business",
                InventoryItem.owner_id == biz.id,
            )
        )
        total_used = int(used_result.scalar_one())
        if total_used <= high_mark:
            continue

        owner = agents_map.get(str(biz.owner_id))
        units_to_drain = total_used - low_mark

        # Drain from each inventory item proportionally
        inv_result = await db.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "business",
                InventoryItem.owner_id == biz.id,
                InventoryItem.quantity > 0,
            )
        )
        items = list(inv_result.scalars().all())

        drained_total = 0
        revenue = Decimal("0")
        for item in items:
            if drained_total >= units_to_drain:
                break
            drain_qty = min(item.quantity, units_to_drain - drained_total)
            item.quantity -= drain_qty
            drained_total += drain_qty

            base_val = Decimal(str(goods_config.get(item.good_slug, {}).get("base_value", 1)))
            revenue += base_val * DRAIN_SALE_DISCOUNT * drain_qty

        # Credit revenue to NPC owner (from central bank)
        if owner is not None and revenue > 0 and central_bank is not None:
            bank_reserves = Decimal(str(central_bank.reserves))
            payout = min(revenue, bank_reserves)
            if payout > 0:
                owner.balance = Decimal(str(owner.balance)) + payout
                central_bank.reserves = bank_reserves - payout

        if drained_total > 0:
            drain_log.append(
                {
                    "business": biz.name,
                    "units_drained": drained_total,
                    "revenue": float(revenue),
                }
            )
            logger.debug(
                "Drained %d units from NPC %r (revenue: %.2f)",
                drained_total,
                biz.name,
                float(revenue),
            )

    return drain_log


def _check_profitability(
    npc_businesses: list[Business],
    agents_map: dict[str, Agent],
    now,
) -> list[dict]:
    """Step 2: Close NPC businesses with deeply negative balances."""
    closures = []
    for biz in npc_businesses:
        owner = agents_map.get(str(biz.owner_id))
        if owner is None:
            continue
        biz_balance = Decimal(str(owner.balance))
        if biz_balance < Decimal("-500"):
            biz.closed_at = now
            closures.append(
                {
                    "business": biz.name,
                    "reason": "unprofitable",
                    "balance": float(biz_balance),
                }
            )
            logger.info(
                "Closed NPC business %r (balance: %.2f)",
                biz.name,
                float(biz_balance),
            )
    return closures


async def _adjust_prices(
    db: AsyncSession,
    settings: Settings,
) -> list[dict]:
    """Step 4: Adjust NPC storefront prices — market-aware and competition-aware.

    Strategy:
    - If player businesses sell the same good in the same zone, NPC retreats
      toward ``reference_price * 1.1`` instead of undercutting.
    - If marketplace sell orders exist, NPC uses the avg market price as a signal.
    - Falls back to inventory-based adjustments when no market context exists.
    """
    from backend.models.marketplace import MarketOrder

    demand_entries = settings.npc_demand.get("npc_demand", [])

    active_npc_result = await db.execute(
        select(Business).where(
            Business.is_npc == True,  # noqa: E712
            Business.closed_at.is_(None),
        )
    )
    active_npc_businesses = list(active_npc_result.scalars().all())

    prices_result = await db.execute(select(StorefrontPrice))
    prices_by_biz: dict[str, list[StorefrontPrice]] = {}
    for sp in prices_result.scalars().all():
        prices_by_biz.setdefault(str(sp.business_id), []).append(sp)

    # Pre-load player storefront competition: {(zone_id, good_slug): count}
    player_competition: dict[tuple, int] = {}
    player_sp_result = await db.execute(
        select(StorefrontPrice.good_slug, Business.zone_id, func.count(StorefrontPrice.id))
        .join(Business, Business.id == StorefrontPrice.business_id)
        .where(
            Business.is_npc.is_(False),
            Business.closed_at.is_(None),
        )
        .group_by(StorefrontPrice.good_slug, Business.zone_id)
    )
    for row in player_sp_result.all():
        player_competition[(str(row[1]), row[0])] = row[2]

    # Pre-load average marketplace sell prices per good
    market_avg_result = await db.execute(
        select(MarketOrder.good_slug, func.avg(MarketOrder.price))
        .where(
            MarketOrder.side == "sell",
            MarketOrder.status.in_(["open", "partially_filled"]),
        )
        .group_by(MarketOrder.good_slug)
    )
    market_avg_prices: dict[str, float] = {row[0]: float(row[1]) for row in market_avg_result.all()}

    price_adjustments: list[dict] = []

    for biz in active_npc_businesses:
        for sp in prices_by_biz.get(str(biz.id), []):
            good_slug = sp.good_slug
            current_price = Decimal(str(sp.price))

            inv_result = await db.execute(
                select(InventoryItem).where(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == biz.id,
                    InventoryItem.good_slug == good_slug,
                )
            )
            inv_item = inv_result.scalar_one_or_none()
            current_stock = inv_item.quantity if inv_item else 0

            demand_cfg = next((e for e in demand_entries if e.get("good") == good_slug), None)
            if demand_cfg is None:
                continue

            base_demand = float(demand_cfg.get("base_demand_per_zone", 1))
            ref_price = float(demand_cfg.get("reference_price", float(current_price)))

            zone_result = await db.execute(select(Zone).where(Zone.id == biz.zone_id))
            biz_zone = zone_result.scalar_one_or_none()
            zone_mult = float(biz_zone.foot_traffic) if biz_zone else 1.0
            expected = base_demand * zone_mult

            ticks_of_stock = current_stock / expected if expected > 0 else float("inf")

            # Check for player competition in this zone
            zone_id_str = str(biz.zone_id) if biz.zone_id else ""
            has_player_competition = player_competition.get((zone_id_str, good_slug), 0) > 0

            # Get market context
            market_price = market_avg_prices.get(good_slug)

            new_price = current_price

            if has_player_competition:
                # Players sell this good here — NPC retreats toward reference_price * 1.1
                # to avoid undercutting players while still being available
                retreat_target = Decimal(str(ref_price * 1.1))
                if current_price < retreat_target:
                    new_price = current_price * Decimal(str(PRICE_INCREASE_FACTOR))
                    new_price = min(new_price, retreat_target)
                elif current_price > retreat_target * Decimal("1.3"):
                    # Too far above target, come down slowly
                    new_price = current_price * Decimal(str(PRICE_REDUCTION_FACTOR))
            elif current_stock == 0:
                new_price = current_price * Decimal(str(PRICE_INCREASE_FACTOR))
                new_price = min(new_price, Decimal(str(ref_price * 3.0)))
            elif ticks_of_stock > 5:
                new_price = current_price * Decimal(str(PRICE_REDUCTION_FACTOR))
                new_price = max(new_price, MIN_PRICE)
                new_price = max(new_price, Decimal(str(ref_price * 0.5)))
            elif market_price is not None:
                # Align toward market price if significantly different
                market_dec = Decimal(str(market_price))
                if current_price > market_dec * Decimal("1.3"):
                    new_price = current_price * Decimal(str(PRICE_REDUCTION_FACTOR))
                elif current_price < market_dec * Decimal("0.7") and current_stock < expected * 2:
                    new_price = current_price * Decimal(str(PRICE_INCREASE_FACTOR))

            new_price = max(new_price, MIN_PRICE)
            new_price = round(new_price, 2)
            if new_price != current_price:
                sp.price = float(new_price)
                price_adjustments.append(
                    {
                        "business": biz.name,
                        "good": good_slug,
                        "old_price": float(current_price),
                        "new_price": float(new_price),
                        "stock": current_stock,
                        "player_competition": has_player_competition,
                    }
                )

    return price_adjustments


async def _adjust_job_wages(
    db: AsyncSession,
    npc_businesses: list[Business],
    activity_factor: float,
    settings: Settings,
) -> list[dict]:
    """Step 6: Adjust NPC job wages based on online player count.

    When few players are online, boost wages to attract them.
    When many players are online, return to default wages.
    """
    default_wage = float(getattr(settings.economy, "default_wage_per_work_call", 30))
    wage_boost = float(getattr(settings.economy, "npc_job_wage_boost_factor", 1.5))
    # activity_factor is high (1.0) when 0 players → boost wages to attract them
    # activity_factor is low (0.1) when many players → wages at default
    target_wage = round(default_wage * (1 + activity_factor * (wage_boost - 1)), 2)

    npc_biz_ids = [biz.id for biz in npc_businesses]
    if not npc_biz_ids:
        return []

    jobs_result = await db.execute(
        select(JobPosting).where(
            JobPosting.business_id.in_(npc_biz_ids),
            JobPosting.is_active == True,  # noqa: E712
        )
    )
    jobs = list(jobs_result.scalars().all())

    adjustments: list[dict] = []
    for job in jobs:
        old_wage = float(job.wage_per_work)
        if abs(old_wage - target_wage) >= 0.5:
            job.wage_per_work = target_wage
            adjustments.append(
                {
                    "job_title": job.title,
                    "old_wage": old_wage,
                    "new_wage": target_wage,
                }
            )

    return adjustments
