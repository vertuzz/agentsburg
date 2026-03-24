"""
NPC Business Simulation for Agent Economy.

Runs during the slow tick (hourly). Handles all automated NPC business logic:

1. Auto-produce: NPC businesses produce goods each tick using their recipe
   (or directly add goods if no recipe). NPC efficiency = 0.5x.
   (Implementation in npc_production.py)

2. Profitability check: If an NPC business has been unprofitable for
   3+ consecutive periods, close it (it's been outcompeted by players).

3. Demand gap detection: If a good has high NPC demand but no supply,
   spawn a new NPC business to fill the gap.
   (Implementation in npc_production.py)

4. Price adjustment:
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Re-export so existing imports from this module still work
from backend.economy.npc_production import (
    run_npc_production,  # noqa: F401
    spawn_demand_gap_businesses,  # noqa: F401
)
from backend.models.agent import Agent
from backend.models.banking import CentralBank
from backend.models.business import Business, StorefrontPrice
from backend.models.inventory import InventoryItem
from backend.models.recipe import Recipe
from backend.models.zone import Zone

if TYPE_CHECKING:
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


async def simulate_npc_businesses(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Run all NPC business simulation logic for one slow tick.

    Steps:
      1. Auto-produce goods for each NPC business
      2. Check profitability → close chronic losers
      3. Check demand gaps → spawn new NPC businesses
      4. Adjust prices based on inventory levels
    """
    now = clock.now()
    npc_efficiency = float(settings.economy.npc_worker_efficiency)
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

    # --- Step 1: Auto-produce ---
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

    # --- Step 2: Profitability check ---
    closures = _check_profitability(npc_businesses, agents_map, now)

    # --- Step 3: Demand gap spawning ---
    spawned = await spawn_demand_gap_businesses(
        db,
        settings,
        recipes_by_output,
        central_bank,
        now,
    )

    # --- Step 4: Price adjustment ---
    price_adjustments = await _adjust_prices(db, settings)

    await db.flush()

    logger.info(
        "NPC businesses: %d produced, %d closed, %d spawned, %d price adjustments",
        len(production_log),
        len(closures),
        len(spawned),
        len(price_adjustments),
    )

    return {
        "type": "npc_businesses",
        "timestamp": now.isoformat(),
        "production": production_log,
        "closures": closures,
        "spawned": spawned,
        "price_adjustments": price_adjustments,
    }


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
    """Step 4: Adjust NPC storefront prices based on inventory levels."""
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

            new_price = current_price
            if current_stock == 0:
                new_price = current_price * Decimal(str(PRICE_INCREASE_FACTOR))
                new_price = min(new_price, Decimal(str(ref_price * 3.0)))
            elif ticks_of_stock > 5:
                new_price = current_price * Decimal(str(PRICE_REDUCTION_FACTOR))
                new_price = max(new_price, MIN_PRICE)
                new_price = max(new_price, Decimal(str(ref_price * 0.5)))

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
                    }
                )

    return price_adjustments
