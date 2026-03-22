"""
NPC Business Simulation for Agent Economy.

Runs during the slow tick (hourly). Handles all automated NPC business logic:

1. Auto-produce: NPC businesses produce goods each tick using their recipe
   (or directly add goods if no recipe). NPC efficiency = 0.5x.

2. Profitability check: If an NPC business has been unprofitable for
   3+ consecutive periods, close it (it's been outcompeted by players).

3. Demand gap detection: If a good has high NPC demand but no supply,
   spawn a new NPC business to fill the gap.

4. Price adjustment:
   - Inventory growing (not selling fast enough) → reduce prices 5-10%
   - Selling out every tick → increase prices 5-10%

NPC businesses each have an Agent account (named "NPC_Farm_01" etc.) that
receives revenue and pays costs. This lets the banking system track their
balance for closure decisions.
"""

from __future__ import annotations

import logging
import secrets
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.banking import CentralBank
from backend.models.business import Business, StorefrontPrice
from backend.models.inventory import InventoryItem
from backend.models.recipe import Recipe
from backend.models.transaction import Transaction
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
PRICE_REDUCTION_FACTOR = 0.92   # 8% price cut when overstocked
PRICE_INCREASE_FACTOR = 1.08   # 8% price rise when selling out
MIN_PRICE = Decimal("0.50")     # Never price below 0.50


async def simulate_npc_businesses(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Run all NPC business simulation logic for one slow tick.

    Steps:
      1. Auto-produce goods for each NPC business
      2. Check profitability → close chronic losers
      3. Check demand gaps → spawn new NPC businesses
      4. Adjust prices based on inventory levels

    Args:
        db:       Active async database session.
        clock:    Clock for timestamps.
        settings: Application settings.

    Returns:
        Summary dict of all NPC business actions taken.
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

    # Load agents (NPC owners) — needed for balance credits/debits
    agents_result = await db.execute(select(Agent))
    agents_map: dict[str, Agent] = {str(a.id): a for a in agents_result.scalars().all()}

    # Load recipes indexed by output_good
    recipes_result = await db.execute(select(Recipe))
    all_recipes = list(recipes_result.scalars().all())
    recipes_by_slug: dict[str, Recipe] = {r.slug: r for r in all_recipes}
    recipes_by_output: dict[str, list[Recipe]] = {}
    for recipe in all_recipes:
        recipes_by_output.setdefault(recipe.output_good, []).append(recipe)

    # Load bootstrap config for NPC businesses
    bootstrap_cfg = settings.bootstrap
    npc_biz_configs = bootstrap_cfg.get("npc_businesses", [])
    npc_config_by_name: dict[str, dict] = {c["name"]: c for c in npc_biz_configs}

    # Load CentralBank for funding new NPC businesses
    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1))
    central_bank = bank_result.scalar_one_or_none()

    production_log = []
    closures = []
    price_adjustments = []
    spawned = []

    # --- Step 1: Auto-produce for each NPC business ---
    for biz in npc_businesses:
        biz_id_str = str(biz.id)
        owner = agents_map.get(str(biz.owner_id))
        if owner is None:
            continue

        # Find this business's config from bootstrap
        biz_config = npc_config_by_name.get(biz.name, {})
        produces = biz_config.get("produces", [])

        for prod_cfg in produces:
            good_slug = prod_cfg.get("good")
            if not good_slug:
                continue

            quantity_per_tick = int(prod_cfg.get("quantity_per_slow_tick", 0))
            if quantity_per_tick <= 0:
                continue

            recipe_slug = prod_cfg.get("recipe")

            # Apply NPC efficiency to production quantity
            effective_quantity = max(1, int(quantity_per_tick * npc_efficiency))

            # If a recipe is specified, check inputs
            recipe = None
            if recipe_slug:
                recipe = recipes_by_slug.get(recipe_slug)

            inputs_satisfied = True
            inputs_used = []

            if recipe is not None:
                # NPC produces based on recipe — check inputs are available
                # Scale input requirements by effective_quantity / recipe.output_quantity
                input_scale = effective_quantity / max(recipe.output_quantity, 1)

                for inp in (recipe.inputs_json or []):
                    inp_slug = inp.get("good_slug") or inp.get("good")
                    inp_qty_per_batch = int(inp.get("quantity", 1))
                    inp_qty_needed = max(1, int(inp_qty_per_batch * input_scale))

                    # Check business inventory for input
                    inv_result = await db.execute(
                        select(InventoryItem).where(
                            InventoryItem.owner_type == "business",
                            InventoryItem.owner_id == biz.id,
                            InventoryItem.good_slug == inp_slug,
                        )
                    )
                    inv_item = inv_result.scalar_one_or_none()

                    if inv_item is None or inv_item.quantity < inp_qty_needed:
                        inputs_satisfied = False
                        logger.debug(
                            "NPC biz %s: insufficient %s for recipe %s (have %d, need %d)",
                            biz.name, inp_slug, recipe_slug,
                            inv_item.quantity if inv_item else 0, inp_qty_needed
                        )
                        break

                    inputs_used.append((inv_item, inp_qty_needed))

            if not inputs_satisfied:
                continue  # Skip production this tick — no inputs

            # Deduct inputs
            for inv_item, qty_needed in inputs_used:
                inv_item.quantity -= qty_needed

            # Calculate cost for this production (NPC wage equivalent)
            # Simulate a labor cost: npc_worker_wage_multiplier * base wage
            base_wage_per_call = float(getattr(settings.economy, "default_wage_per_work_call", 30))
            labor_cost = Decimal(str(base_wage_per_call * npc_wage_mult))

            # Deduct labor cost from NPC owner balance
            current_balance = Decimal(str(owner.balance))
            if current_balance >= labor_cost:
                owner.balance = current_balance - labor_cost
            else:
                # NPC can't pay full wages — partial deduction (they absorb the loss)
                owner.balance = Decimal("0")

            # Add output to business inventory
            # Check current inventory level to avoid overstock
            out_inv_result = await db.execute(
                select(InventoryItem).where(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == biz.id,
                    InventoryItem.good_slug == good_slug,
                )
            )
            out_inv = out_inv_result.scalar_one_or_none()

            # Don't overfill: cap at business_storage_capacity equivalent
            current_stock = out_inv.quantity if out_inv else 0
            max_stock = settings.economy.business_storage_capacity // 2  # use half capacity

            units_to_add = min(effective_quantity, max(0, max_stock - current_stock))

            if units_to_add > 0:
                if out_inv is None:
                    out_inv = InventoryItem(
                        owner_type="business",
                        owner_id=biz.id,
                        good_slug=good_slug,
                        quantity=units_to_add,
                    )
                    db.add(out_inv)
                else:
                    out_inv.quantity += units_to_add

            production_log.append({
                "business": biz.name,
                "good": good_slug,
                "quantity_produced": units_to_add,
            })

    await db.flush()

    # --- Step 2: Profitability check → close chronic losers ---
    # Re-check NPC businesses after production
    for biz in npc_businesses:
        owner = agents_map.get(str(biz.owner_id))
        if owner is None:
            continue

        biz_balance = Decimal(str(owner.balance))

        # Close if balance is deeply negative (chronic unprofitability)
        # We use a simple heuristic: balance below -500 = struggling significantly
        close_threshold = Decimal("-500")
        if biz_balance < close_threshold:
            biz.closed_at = now
            closures.append({
                "business": biz.name,
                "reason": "unprofitable",
                "balance": float(biz_balance),
            })
            logger.info(
                "Closed NPC business %r (balance: %.2f)",
                biz.name, float(biz_balance)
            )

    # --- Step 3: Demand gap check → spawn new NPC businesses ---
    # Look for goods with high NPC demand but no/low supply
    demand_entries = settings.npc_demand.get("npc_demand", [])

    # Load all zones for spawning
    zones_result = await db.execute(select(Zone))
    zones = {z.slug: z for z in zones_result.scalars().all()}

    # Check which goods have demand but low supply
    spawned_count = 0
    max_spawns_per_tick = 2  # Don't flood the market

    for demand_cfg in demand_entries:
        if spawned_count >= max_spawns_per_tick:
            break

        good_slug = demand_cfg.get("good")
        base_demand = float(demand_cfg.get("base_demand_per_zone", 0))

        if base_demand < 10:  # Only respond to significant demand
            continue

        # Check total supply across all open businesses
        supply_result = await db.execute(
            select(func.sum(InventoryItem.quantity)).where(
                InventoryItem.owner_type == "business",
                InventoryItem.good_slug == good_slug,
                InventoryItem.quantity > 0,
            )
        )
        total_supply = supply_result.scalar() or 0

        # Supply threshold: less than 2 ticks' worth of demand
        num_zones = len(zones)
        ticks_of_supply = total_supply / max(base_demand * num_zones, 1)

        if ticks_of_supply < 0.5:  # Less than half a tick of supply
            # Find a recipe that produces this good
            producing_recipes = recipes_by_output.get(good_slug, [])

            if not producing_recipes:
                continue

            recipe = producing_recipes[0]

            # Determine the best zone for this business
            # For finished goods → suburbs; for raw → outskirts; for intermediate → industrial
            goods_config = {g["slug"]: g for g in settings.goods}
            good_tier = goods_config.get(good_slug, {}).get("tier", 2)
            if good_tier == 1:
                spawn_zone_slug = "outskirts"
            elif good_tier == 2:
                spawn_zone_slug = "industrial"
            else:
                spawn_zone_slug = "suburbs"

            spawn_zone = zones.get(spawn_zone_slug)
            if spawn_zone is None:
                continue

            # Check bank has reserves to fund new NPC business
            if central_bank is None:
                continue

            initial_balance = Decimal("2000")
            if Decimal(str(central_bank.reserves)) < initial_balance:
                continue

            # Create NPC agent for this business
            npc_num = spawned_count + 1
            npc_agent_name = f"NPC_{good_slug.replace('_', '').capitalize()}_{npc_num:02d}_{secrets.token_hex(3)}"
            npc_agent = Agent(
                name=npc_agent_name[:64],
                action_token=f"npc_{secrets.token_urlsafe(32)}",
                view_token=f"npc_{secrets.token_urlsafe(32)}",
                balance=float(initial_balance),
            )
            db.add(npc_agent)
            await db.flush()

            # Create the NPC business
            # Find best business type for this recipe
            biz_type = recipe.bonus_business_type or "workshop"

            new_biz = Business(
                owner_id=npc_agent.id,
                name=f"NPC {good_slug.replace('_', ' ').title()} Co.",
                type_slug=biz_type,
                zone_id=spawn_zone.id,
                storage_capacity=500,
                is_npc=True,
                default_recipe_slug=recipe.slug,
            )
            db.add(new_biz)
            await db.flush()

            # Set storefront price (150% of reference price)
            ref_price = float(demand_cfg.get("reference_price", 10))
            sp = StorefrontPrice(
                business_id=new_biz.id,
                good_slug=good_slug,
                price=round(ref_price * 1.5, 2),
            )
            db.add(sp)

            # Give initial inventory
            initial_stock = min(100, max(10, int(base_demand * 2)))
            inv = InventoryItem(
                owner_type="business",
                owner_id=new_biz.id,
                good_slug=good_slug,
                quantity=initial_stock,
            )
            db.add(inv)

            # Deduct from bank reserves (NPC funding)
            central_bank.reserves = Decimal(str(central_bank.reserves)) - initial_balance

            # Record the disbursement
            txn = Transaction(
                type="loan_disbursement",
                from_agent_id=None,
                to_agent_id=npc_agent.id,
                amount=float(initial_balance),
                metadata_json={
                    "reason": "npc_business_spawn",
                    "good_slug": good_slug,
                    "business_name": new_biz.name,
                    "supply_ticks": ticks_of_supply,
                    "tick_time": now.isoformat(),
                },
            )
            db.add(txn)

            await db.flush()

            spawned.append({
                "business": new_biz.name,
                "good": good_slug,
                "zone": spawn_zone_slug,
                "supply_ticks_before": ticks_of_supply,
            })
            spawned_count += 1

            logger.info(
                "Spawned new NPC business %r in %s for good=%s (supply was %.2f ticks)",
                new_biz.name, spawn_zone_slug, good_slug, ticks_of_supply
            )

    # --- Step 4: Price adjustment based on inventory levels ---
    # Reload NPC businesses (some may have been closed)
    active_npc_result = await db.execute(
        select(Business).where(
            Business.is_npc == True,  # noqa: E712
            Business.closed_at.is_(None),
        )
    )
    active_npc_businesses = list(active_npc_result.scalars().all())

    # Load storefront prices
    prices_result = await db.execute(select(StorefrontPrice))
    prices_by_biz: dict[str, list[StorefrontPrice]] = {}
    for sp in prices_result.scalars().all():
        biz_key = str(sp.business_id)
        prices_by_biz.setdefault(biz_key, []).append(sp)

    # For each NPC business, check inventory levels vs expected demand
    for biz in active_npc_businesses:
        biz_id_str = str(biz.id)
        biz_prices = prices_by_biz.get(biz_id_str, [])

        for sp in biz_prices:
            good_slug = sp.good_slug
            current_price = Decimal(str(sp.price))

            # Get current inventory
            inv_result = await db.execute(
                select(InventoryItem).where(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == biz.id,
                    InventoryItem.good_slug == good_slug,
                )
            )
            inv_item = inv_result.scalar_one_or_none()
            current_stock = inv_item.quantity if inv_item else 0

            # Get demand config for this good
            demand_cfg = None
            for entry in demand_entries:
                if entry.get("good") == good_slug:
                    demand_cfg = entry
                    break

            if demand_cfg is None:
                continue

            base_demand = float(demand_cfg.get("base_demand_per_zone", 1))
            ref_price = float(demand_cfg.get("reference_price", float(current_price)))

            # Determine zone foot traffic for expected demand per tick
            zone_result = await db.execute(
                select(Zone).where(Zone.id == biz.zone_id)
            )
            biz_zone = zone_result.scalar_one_or_none()
            zone_multiplier = float(biz_zone.foot_traffic) if biz_zone else 1.0

            expected_demand_per_tick = base_demand * zone_multiplier

            # Price adjustment logic:
            # - Too much stock (> 3 ticks supply): lower prices to attract buyers
            # - Out of stock (== 0): raise prices (would have sold more)
            # - Just right: no change

            if expected_demand_per_tick > 0:
                ticks_of_stock = current_stock / expected_demand_per_tick
            else:
                ticks_of_stock = float("inf")

            new_price = current_price

            if current_stock == 0:
                # Sold out — can raise prices a bit
                new_price = current_price * Decimal(str(PRICE_INCREASE_FACTOR))
                # Don't go above 3x reference price
                max_price = Decimal(str(ref_price * 3.0))
                new_price = min(new_price, max_price)
            elif ticks_of_stock > 5:
                # Very overstocked — lower prices
                new_price = current_price * Decimal(str(PRICE_REDUCTION_FACTOR))
                new_price = max(new_price, MIN_PRICE)
                # Don't go below 50% of reference price
                min_floor = Decimal(str(ref_price * 0.5))
                new_price = max(new_price, min_floor)

            # Round to 2 decimal places
            new_price = round(new_price, 2)

            if new_price != current_price:
                sp.price = float(new_price)
                price_adjustments.append({
                    "business": biz.name,
                    "good": good_slug,
                    "old_price": float(current_price),
                    "new_price": float(new_price),
                    "stock": current_stock,
                })

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
