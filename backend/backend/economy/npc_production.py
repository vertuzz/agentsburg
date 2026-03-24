"""
NPC Production & Spawning Logic for Agent Economy.

Extracted from npc_businesses.py. Handles:
  - Auto-produce: NPC businesses produce goods each tick using their recipe.
  - Auto-stock: Buy missing recipe inputs from the central bank at base_value.
  - Demand gap spawning: Create new NPC businesses for under-supplied goods.
"""

from __future__ import annotations

import contextlib
import logging
import secrets
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from backend.models.agent import Agent
from backend.models.business import Business, JobPosting, StorefrontPrice
from backend.models.inventory import InventoryItem
from backend.models.marketplace import MarketOrder
from backend.models.transaction import Transaction
from backend.models.zone import Zone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.config import Settings
    from backend.models.banking import CentralBank
    from backend.models.recipe import Recipe

logger = logging.getLogger(__name__)


async def _auto_restock_inputs(
    db: AsyncSession,
    biz: Business,
    recipe: Recipe,
    effective_quantity: int,
    central_bank: CentralBank,
    settings: Settings,
) -> tuple[bool, list[tuple[InventoryItem, int]]]:
    """Buy missing recipe inputs from the central bank at base_value."""
    goods_config = {g["slug"]: g for g in settings.goods}
    input_scale = effective_quantity / max(recipe.output_quantity, 1)
    restock_cost = Decimal("0")
    restock_items: list[tuple[str, int]] = []

    for inp in recipe.inputs_json or []:
        inp_slug = inp.get("good_slug") or inp.get("good")
        inp_qty_needed = max(1, int(int(inp.get("quantity", 1)) * input_scale))
        inv_r = await db.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "business",
                InventoryItem.owner_id == biz.id,
                InventoryItem.good_slug == inp_slug,
            )
        )
        inv_i = inv_r.scalar_one_or_none()
        shortfall = max(0, inp_qty_needed - (inv_i.quantity if inv_i else 0))
        if shortfall > 0:
            unit_cost = Decimal(str(goods_config.get(inp_slug, {}).get("base_value", 1)))
            restock_cost += unit_cost * shortfall
            restock_items.append((inp_slug, shortfall))

    if restock_cost > Decimal(str(central_bank.reserves)) or not restock_items:
        return False, []

    central_bank.reserves = Decimal(str(central_bank.reserves)) - restock_cost
    from backend.agents.inventory import add_to_inventory as _add_inv

    for inp_slug, shortfall in restock_items:
        with contextlib.suppress(ValueError):
            await _add_inv(db, "business", biz.id, inp_slug, shortfall, settings)
    await db.flush()

    # Re-check inputs after restocking
    inputs_used: list[tuple[InventoryItem, int]] = []
    for inp in recipe.inputs_json or []:
        inp_slug = inp.get("good_slug") or inp.get("good")
        inp_qty_needed = max(1, int(int(inp.get("quantity", 1)) * input_scale))
        inv_r = await db.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "business",
                InventoryItem.owner_id == biz.id,
                InventoryItem.good_slug == inp_slug,
            )
        )
        inv_i = inv_r.scalar_one_or_none()
        if inv_i is None or inv_i.quantity < inp_qty_needed:
            return False, []
        inputs_used.append((inv_i, inp_qty_needed))
    return True, inputs_used


async def run_npc_production(
    db: AsyncSession,
    npc_businesses: list[Business],
    agents_map: dict[str, Agent],
    recipes_by_slug: dict[str, Recipe],
    npc_config_by_name: dict[str, dict],
    central_bank: CentralBank | None,
    npc_efficiency: float,
    npc_wage_mult: float,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Auto-produce goods for each NPC business (Step 1 of the slow tick)."""
    production_log: list[dict[str, Any]] = []

    for biz in npc_businesses:
        owner = agents_map.get(str(biz.owner_id))
        if owner is None:
            continue
        for prod_cfg in npc_config_by_name.get(biz.name, {}).get("produces", []):
            good_slug = prod_cfg.get("good")
            quantity_per_tick = int(prod_cfg.get("quantity_per_slow_tick", 0))
            if not good_slug or quantity_per_tick <= 0:
                continue
            effective_quantity = max(1, int(quantity_per_tick * npc_efficiency))
            recipe = recipes_by_slug.get(prod_cfg.get("recipe", ""))

            inputs_satisfied, inputs_used = True, []
            if recipe is not None:
                input_scale = effective_quantity / max(recipe.output_quantity, 1)
                for inp in recipe.inputs_json or []:
                    inp_slug = inp.get("good_slug") or inp.get("good")
                    inp_qty_needed = max(1, int(int(inp.get("quantity", 1)) * input_scale))
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
                            biz.name,
                            inp_slug,
                            prod_cfg.get("recipe"),
                            inv_item.quantity if inv_item else 0,
                            inp_qty_needed,
                        )
                        break
                    inputs_used.append((inv_item, inp_qty_needed))

            if not inputs_satisfied:
                if recipe is not None and central_bank is not None:
                    inputs_satisfied, inputs_used = await _auto_restock_inputs(
                        db,
                        biz,
                        recipe,
                        effective_quantity,
                        central_bank,
                        settings,
                    )
                if not inputs_satisfied:
                    continue

            for inv_item, qty_needed in inputs_used:
                inv_item.quantity -= qty_needed

            # Deduct labor cost
            base_wage = float(getattr(settings.economy, "default_wage_per_work_call", 30))
            labor_cost = Decimal(str(base_wage * npc_wage_mult))
            bal = Decimal(str(owner.balance))
            owner.balance = max(Decimal("0"), bal - labor_cost)

            # Add output (capped at half storage capacity)
            out_inv_result = await db.execute(
                select(InventoryItem).where(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == biz.id,
                    InventoryItem.good_slug == good_slug,
                )
            )
            out_inv = out_inv_result.scalar_one_or_none()
            current_stock = out_inv.quantity if out_inv else 0
            max_stock = settings.economy.business_storage_capacity // 2
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
            production_log.append(
                {
                    "business": biz.name,
                    "good": good_slug,
                    "quantity_produced": units_to_add,
                }
            )

    await db.flush()
    return production_log


async def spawn_demand_gap_businesses(
    db: AsyncSession,
    settings: Settings,
    recipes_by_output: dict[str, list[Recipe]],
    central_bank: CentralBank | None,
    now,
) -> list[dict]:
    """Spawn new NPC businesses for goods with high demand but low supply."""
    demand_entries = settings.npc_demand.get("npc_demand", [])
    zones_result = await db.execute(select(Zone))
    zones = {z.slug: z for z in zones_result.scalars().all()}

    spawned: list[dict] = []
    max_spawns_per_tick = 2

    for demand_cfg in demand_entries:
        if len(spawned) >= max_spawns_per_tick:
            break
        good_slug = demand_cfg.get("good")
        base_demand = float(demand_cfg.get("base_demand_per_zone", 0))
        if base_demand < 10:
            continue

        supply_result = await db.execute(
            select(func.sum(InventoryItem.quantity)).where(
                InventoryItem.owner_type == "business",
                InventoryItem.good_slug == good_slug,
                InventoryItem.quantity > 0,
            )
        )
        sell_order_result = await db.execute(
            select(func.sum(MarketOrder.quantity_total - MarketOrder.quantity_filled)).where(
                MarketOrder.good_slug == good_slug,
                MarketOrder.side == "sell",
                MarketOrder.status.in_(["open", "partially_filled"]),
            )
        )
        total_supply = (supply_result.scalar() or 0) + (sell_order_result.scalar() or 0)
        ticks_of_supply = total_supply / max(base_demand * len(zones), 1)
        if ticks_of_supply >= 0.5:
            continue

        producing_recipes = recipes_by_output.get(good_slug, [])
        if not producing_recipes:
            continue
        recipe = producing_recipes[0]

        goods_config = {g["slug"]: g for g in settings.goods}
        good_tier = goods_config.get(good_slug, {}).get("tier", 2)
        spawn_zone_slug = {1: "outskirts", 2: "industrial"}.get(good_tier, "suburbs")
        spawn_zone = zones.get(spawn_zone_slug)
        if spawn_zone is None or central_bank is None:
            continue

        initial_balance = Decimal("2000")
        if Decimal(str(central_bank.reserves)) < initial_balance:
            continue

        npc_num = len(spawned) + 1
        npc_name = f"NPC_{good_slug.replace('_', '').capitalize()}_{npc_num:02d}_{secrets.token_hex(3)}"
        npc_agent = Agent(
            name=npc_name[:64],
            balance=float(initial_balance),
            action_token=f"npc_{secrets.token_urlsafe(32)}",
            view_token=f"npc_{secrets.token_urlsafe(32)}",
        )
        db.add(npc_agent)
        await db.flush()

        new_biz = Business(
            owner_id=npc_agent.id,
            name=f"NPC {good_slug.replace('_', ' ').title()} Co.",
            type_slug=recipe.bonus_business_type or "workshop",
            zone_id=spawn_zone.id,
            storage_capacity=500,
            is_npc=True,
            default_recipe_slug=recipe.slug,
        )
        db.add(new_biz)
        await db.flush()

        ref_price = float(demand_cfg.get("reference_price", 10))
        db.add(
            StorefrontPrice(
                business_id=new_biz.id,
                good_slug=good_slug,
                price=round(ref_price * 1.5, 2),
            )
        )
        db.add(
            InventoryItem(
                owner_type="business",
                owner_id=new_biz.id,
                good_slug=good_slug,
                quantity=min(100, max(10, int(base_demand * 2))),
            )
        )
        default_wage = float(getattr(settings.economy, "default_wage_per_work_call", 30))
        db.add(
            JobPosting(
                business_id=new_biz.id,
                product_slug=good_slug,
                title=f"{good_slug.replace('_', ' ').title()} Worker",
                wage_per_work=default_wage,
                max_workers=3,
                is_active=True,
            )
        )

        central_bank.reserves = Decimal(str(central_bank.reserves)) - initial_balance
        db.add(
            Transaction(
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
        )
        await db.flush()

        spawned.append(
            {
                "business": new_biz.name,
                "good": good_slug,
                "zone": spawn_zone_slug,
                "supply_ticks_before": ticks_of_supply,
            }
        )
        logger.info(
            "Spawned NPC business %r in %s for good=%s (supply was %.2f ticks)",
            new_biz.name,
            spawn_zone_slug,
            good_slug,
            ticks_of_supply,
        )

    return spawned
