"""
NPC Consumer Simulation for Agent Economy.

Each fast tick, simulated NPC residents walk into storefronts and buy goods.

Key design:
- Demand is per-zone, per-good, computed from npc_demand.yaml
- Demand is scaled by the zone's foot_traffic_multiplier * demand_multiplier
- Actual effective demand also uses a price-responsiveness curve:
    effective_demand = base_demand * (reference_price / actual_price)^elasticity
  But here "actual_price" is undefined when multiple businesses sell at different
  prices, so we use the AVERAGE price across businesses in the zone as the
  price signal, then distribute demand weighted by individual prices.

Distribution among businesses:
  weight(business) = 1 / (price ^ elasticity)
  share(business)  = weight / sum(all_weights)
  demand(business) = total_demand * share

This means cheaper businesses get more customers, but expensive ones still get
some — it's a soft distribution, not winner-take-all.

Transactions are AGGREGATE: one Transaction(type="storefront") per business per
good per tick. Not one per individual NPC customer.

NPC demand vanishes if no businesses are selling — no carry-over to next tick.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.banking import CentralBank
from backend.models.business import Business, StorefrontPrice
from backend.models.inventory import InventoryItem
from backend.models.transaction import Transaction
from backend.models.zone import Zone

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def simulate_npc_purchases(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Simulate NPC consumer purchases across all zones and goods.

    For each zone:
      - For each good with NPC demand config:
        - Calculate total zone demand (base * zone multipliers * price elasticity)
        - Find open businesses in that zone selling this good (with stock)
        - Distribute demand weighted by price (cheaper = more customers)
        - Execute purchases: deduct inventory, credit owner balance, create Transaction

    Args:
        db:       Active async database session.
        clock:    Clock for transaction timestamps.
        settings: Application settings with npc_demand config.

    Returns:
        Summary dict of all purchases made.
    """
    now = clock.now()
    npc_demand_config = settings.npc_demand

    # Parse demand config into a dict keyed by good slug
    demand_entries = npc_demand_config.get("npc_demand", [])
    demand_by_good: dict[str, dict] = {}
    for entry in demand_entries:
        good = entry.get("good")
        if good:
            demand_by_good[good] = entry

    if not demand_by_good:
        return {
            "type": "npc_purchases",
            "timestamp": now.isoformat(),
            "total_transactions": 0,
            "total_revenue": 0.0,
            "purchases": [],
        }

    # Load all zones
    zones_result = await db.execute(select(Zone))
    zones = list(zones_result.scalars().all())

    # Load all open businesses (not closed) with their zone_id
    businesses_result = await db.execute(select(Business).where(Business.closed_at.is_(None)))
    open_businesses = list(businesses_result.scalars().all())

    # Build index: zone_id -> list of businesses
    businesses_by_zone: dict = {}
    for biz in open_businesses:
        zone_key = str(biz.zone_id)
        if zone_key not in businesses_by_zone:
            businesses_by_zone[zone_key] = []
        businesses_by_zone[zone_key].append(biz)

    # Load all storefront prices
    prices_result = await db.execute(select(StorefrontPrice))
    all_prices = list(prices_result.scalars().all())

    # Build index: business_id -> {good_slug -> price}
    prices_by_business: dict[str, dict[str, float]] = {}
    for sp in all_prices:
        biz_key = str(sp.business_id)
        if biz_key not in prices_by_business:
            prices_by_business[biz_key] = {}
        prices_by_business[biz_key][sp.good_slug] = float(sp.price)

    # Load all business inventory items
    inv_result = await db.execute(
        select(InventoryItem).where(
            InventoryItem.owner_type == "business",
            InventoryItem.quantity > 0,
        )
    )
    all_inventory = list(inv_result.scalars().all())

    # Build index: (business_id, good_slug) -> InventoryItem
    inventory_map: dict[tuple[str, str], InventoryItem] = {}
    for item in all_inventory:
        inventory_map[(str(item.owner_id), item.good_slug)] = item

    # Load agent balances for credits (owner_id -> Agent)
    agents_result = await db.execute(select(Agent))
    agents_map: dict[str, Agent] = {str(a.id): a for a in agents_result.scalars().all()}

    # Load central bank — NPC purchases are funded from bank reserves
    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1).with_for_update())
    central_bank = bank_result.scalar_one_or_none()

    all_purchases = []
    total_transactions = 0
    total_revenue_float = 0.0

    for zone in zones:
        zone_id_str = str(zone.id)
        zone_businesses = businesses_by_zone.get(zone_id_str, [])

        foot_traffic = float(zone.foot_traffic)
        demand_multiplier = float(zone.demand_multiplier)

        for good_slug, demand_cfg in demand_by_good.items():
            base_demand: float = float(demand_cfg.get("base_demand_per_zone", 0))
            elasticity: float = float(demand_cfg.get("price_elasticity", 1.0))
            reference_price: float = float(demand_cfg.get("reference_price", 1.0))

            if base_demand <= 0:
                continue

            # Find businesses in this zone that sell this good AND have stock
            selling_businesses = []
            for biz in zone_businesses:
                biz_id_str = str(biz.id)
                biz_prices = prices_by_business.get(biz_id_str, {})
                if good_slug not in biz_prices:
                    continue
                inv_item = inventory_map.get((biz_id_str, good_slug))
                if inv_item is None or inv_item.quantity <= 0:
                    continue
                price = biz_prices[good_slug]
                if price <= 0:
                    continue
                selling_businesses.append((biz, price, inv_item))

            if not selling_businesses:
                # No supply — demand vanishes, no carry-over
                continue

            # Apply price floor: treat prices below 30% of reference_price
            # as ref_price * 0.3 for demand calculation (prevents predatory underpricing)
            price_floor = reference_price * 0.3
            floored_prices = [max(p, price_floor) for _, p, _ in selling_businesses]

            # Calculate average price for demand elasticity (using floored prices)
            avg_price = sum(floored_prices) / len(floored_prices)
            if avg_price <= 0:
                avg_price = reference_price

            # Apply price elasticity to get effective demand
            # More than reference_price → demand drops; below → demand rises
            # Cap amplification at 2.0x to prevent infinite demand from underpricing
            price_ratio = reference_price / avg_price
            amplification = min(price_ratio**elasticity, 2.0)
            effective_demand = base_demand * amplification

            # Scale by zone multipliers
            effective_demand *= foot_traffic * demand_multiplier

            if effective_demand <= 0:
                continue

            # Compute price-weighted distribution
            # weight = 1 / (price ^ elasticity) — cheaper businesses get more
            weights = []
            for biz, price, inv_item in selling_businesses:
                w = 1.0 / max(price**elasticity, 1e-9)
                weights.append(w)

            total_weight = sum(weights)
            if total_weight <= 0:
                continue

            # Execute purchases for each business
            remaining_demand = effective_demand
            for i, (biz, price, inv_item) in enumerate(selling_businesses):
                if remaining_demand <= 0:
                    break

                share = weights[i] / total_weight
                business_demand = effective_demand * share

                # How many units can actually be sold (limited by inventory)
                available = inv_item.quantity
                units_to_sell = min(int(business_demand), available)
                # Allow fractional demand to accumulate toward 1 unit
                if units_to_sell == 0 and business_demand >= 0.5 and available >= 1:
                    units_to_sell = 1

                if units_to_sell <= 0:
                    continue

                # Execute the sale
                revenue = Decimal(str(price)) * units_to_sell

                # NPC purchases are funded from central bank reserves.
                # If the bank can't afford it, skip this purchase.
                if central_bank is not None:
                    bank_reserves = Decimal(str(central_bank.reserves))
                    if bank_reserves < revenue:
                        # Bank can't fund this NPC purchase — skip
                        continue
                    central_bank.reserves = bank_reserves - revenue

                inv_item.quantity -= units_to_sell

                # Credit the business owner's balance
                owner_id_str = str(biz.owner_id)
                owner = agents_map.get(owner_id_str)
                if owner is not None:
                    owner.balance = Decimal(str(owner.balance)) + revenue

                # Create aggregate transaction (one per business per good per tick)
                txn = Transaction(
                    type="storefront",
                    from_agent_id=None,  # NPCs — no individual agent identity
                    to_agent_id=biz.owner_id,
                    amount=float(revenue),
                    metadata_json={
                        "business_id": str(biz.id),
                        "business_name": biz.name,
                        "good_slug": good_slug,
                        "zone_slug": zone.slug,
                        "units_sold": units_to_sell,
                        "price_per_unit": price,
                        "tick_time": now.isoformat(),
                    },
                )
                db.add(txn)

                all_purchases.append(
                    {
                        "business_id": str(biz.id),
                        "business_name": biz.name,
                        "good_slug": good_slug,
                        "zone_slug": zone.slug,
                        "units_sold": units_to_sell,
                        "price": price,
                        "revenue": float(revenue),
                    }
                )

                total_transactions += 1
                total_revenue_float += float(revenue)

                remaining_demand -= units_to_sell

    await db.flush()

    logger.info(
        "NPC purchases: %d transactions, total revenue %.2f",
        total_transactions,
        total_revenue_float,
    )

    return {
        "type": "npc_purchases",
        "timestamp": now.isoformat(),
        "total_transactions": total_transactions,
        "total_revenue": total_revenue_float,
        "purchases": all_purchases,
    }
