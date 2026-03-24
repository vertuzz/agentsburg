"""
NPC Marketplace Demand — Central Bank buys raw goods from the marketplace.

Without this, the marketplace has zero buy-side liquidity for tier-1 goods.
New agents gather resources and list them, but nobody buys. This module
fills that gap: the central bank acts as "buyer of last resort", purchasing
raw-good sell orders at or below reference prices each fast tick.

How it works:
  1. Each fast tick, scan open sell orders for tier-1 (gatherable) goods
  2. For orders priced at or below reference_price from npc_demand.yaml,
     fill them directly using central bank reserves
  3. Goods are "consumed" (removed from economy — NPCs use them)
  4. Seller gets paid, bank reserves decrease
  5. Demand is capped per good per tick to prevent draining the bank

This gives gatherers a guaranteed market for their goods and provides
a price floor for the raw goods economy.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.banking import CentralBank
from backend.models.marketplace import MarketOrder
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

# Base minimum units the bank will buy per good per tick.
# Scales up with active player count: max(20, active_agents * 3).
_BASE_BUY_PER_GOOD_PER_TICK = 20


async def simulate_npc_marketplace_demand(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Central bank buys raw goods from marketplace sell orders.

    Scans open sell orders for gatherable (tier-1) goods priced at or below
    the NPC reference price. Fills them using central bank reserves.
    Buy cap scales with active player population.

    Args:
        db:       Active async database session.
        clock:    Clock for timestamps.
        settings: Application settings with goods and npc_demand config.

    Returns:
        Summary dict of purchases made.
    """
    now = clock.now()

    # Scale buy cap with active player population
    from sqlalchemy import func

    active_count_result = await db.execute(
        select(func.count(Agent.id)).where(
            Agent.is_active == True,  # noqa: E712
        )
    )
    active_agents = active_count_result.scalar_one() or 0
    max_buy_per_good = max(_BASE_BUY_PER_GOOD_PER_TICK, active_agents * 3)

    # Build lookup: good_slug -> reference_price from npc_demand config
    npc_demand_config = settings.npc_demand
    demand_entries = npc_demand_config.get("npc_demand", [])
    ref_prices: dict[str, Decimal] = {}
    for entry in demand_entries:
        good = entry.get("good")
        ref_price = entry.get("reference_price")
        if good and ref_price is not None:
            ref_prices[good] = Decimal(str(ref_price))

    # Get gatherable (tier-1) good slugs
    gatherable_slugs = set()
    for good in settings.goods:
        if good.get("gatherable", False):
            gatherable_slugs.add(good["slug"])

    # Only buy goods that are both gatherable AND have NPC demand config
    target_goods = gatherable_slugs & set(ref_prices.keys())

    if not target_goods:
        return _empty_result(now)

    # Load central bank (locked for update)
    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1).with_for_update())
    central_bank = bank_result.scalar_one_or_none()
    if central_bank is None:
        return _empty_result(now)

    bank_reserves = Decimal(str(central_bank.reserves))
    if bank_reserves <= 0:
        return _empty_result(now)

    total_purchases = 0
    total_spent = Decimal("0")
    purchase_details = []

    for good_slug in sorted(target_goods):
        max_price = ref_prices[good_slug]
        remaining_budget = min(
            bank_reserves - total_spent,
            max_price * max_buy_per_good,
        )
        if remaining_budget <= 0:
            continue

        # Find open sell orders at or below reference price
        sell_result = await db.execute(
            select(MarketOrder)
            .where(
                MarketOrder.good_slug == good_slug,
                MarketOrder.side == "sell",
                MarketOrder.status.in_(["open", "partially_filled"]),
                MarketOrder.price <= float(max_price),
            )
            .order_by(MarketOrder.price.asc(), MarketOrder.created_at.asc())
            .with_for_update()
        )
        sell_orders = list(sell_result.scalars().all())

        units_bought_this_good = 0

        for order in sell_orders:
            if units_bought_this_good >= max_buy_per_good:
                break

            sell_price = Decimal(str(order.price))
            unfilled = order.quantity_total - order.quantity_filled
            if unfilled <= 0:
                continue

            # How many units can we buy?
            can_buy = min(
                unfilled,
                max_buy_per_good - units_bought_this_good,
            )
            cost = sell_price * can_buy
            if cost > (bank_reserves - total_spent):
                # Reduce to what we can afford
                can_buy = int((bank_reserves - total_spent) / sell_price)
                if can_buy <= 0:
                    break
                cost = sell_price * can_buy

            # Load seller agent (locked)
            seller_result = await db.execute(select(Agent).where(Agent.id == order.agent_id).with_for_update())
            seller = seller_result.scalar_one_or_none()
            if seller is None:
                order.status = "cancelled"
                continue

            # Execute the fill
            # 1. Pay seller
            seller.balance = Decimal(str(seller.balance)) + cost

            # 2. Update order
            order.quantity_filled += can_buy
            if order.quantity_filled >= order.quantity_total:
                order.status = "filled"
            else:
                order.status = "partially_filled"

            # 3. Record transaction (no MarketTrade — bank is direct buyer)
            txn = Transaction(
                type="marketplace",
                from_agent_id=None,  # Central bank (NPC buyer)
                to_agent_id=seller.id,
                amount=float(cost),
                metadata_json={
                    "good_slug": good_slug,
                    "quantity": can_buy,
                    "price_per_unit": float(sell_price),
                    "sell_order_id": str(order.id),
                    "npc_buyer": True,
                    "tick_time": now.isoformat(),
                },
            )
            db.add(txn)

            total_spent += cost
            units_bought_this_good += can_buy
            total_purchases += 1

            logger.debug(
                "NPC marketplace buy: %dx %s @ %.2f from %s",
                can_buy,
                good_slug,
                float(sell_price),
                seller.name,
            )

        if units_bought_this_good > 0:
            purchase_details.append(
                {
                    "good_slug": good_slug,
                    "units_bought": units_bought_this_good,
                    "max_price": float(max_price),
                }
            )

    # Deduct total from bank reserves
    if total_spent > 0:
        central_bank.reserves = Decimal(str(central_bank.reserves)) - total_spent
        await db.flush()

    logger.info(
        "NPC marketplace demand: %d fills, spent %.2f from bank reserves",
        total_purchases,
        float(total_spent),
    )

    return {
        "type": "npc_marketplace",
        "timestamp": now.isoformat(),
        "total_fills": total_purchases,
        "total_spent": float(total_spent),
        "purchases": purchase_details,
    }


async def place_npc_buy_orders(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Place visible NPC buy orders on the marketplace for tier-1 and tier-2 goods.

    Without visible buy orders, agents see zero demand on the marketplace and
    think it's dead.  This function ensures there are always standing buy orders
    from the central bank at reference_price so agents can see that demand exists
    and sell into it.

    Runs every fast tick.  Orders placed by the bank use a sentinel agent_id and
    are funded from bank reserves.  Old unfilled NPC orders are cleaned up first.
    """
    import uuid as _uuid

    clock.now()

    NPC_BUYER_SENTINEL = _uuid.UUID("00000000-0000-0000-0000-000000000002")

    # Clean up old NPC buy orders (replace each tick with fresh ones)
    old_result = await db.execute(
        select(MarketOrder)
        .where(
            MarketOrder.agent_id == NPC_BUYER_SENTINEL,
            MarketOrder.side == "buy",
            MarketOrder.status.in_(["open", "partially_filled"]),
        )
        .with_for_update()
    )
    old_orders = list(old_result.scalars().all())
    for old in old_orders:
        old.status = "cancelled"

    # Load central bank
    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1).with_for_update())
    central_bank = bank_result.scalar_one_or_none()
    if central_bank is None:
        return {"type": "npc_buy_orders", "orders_placed": 0}

    bank_reserves = Decimal(str(central_bank.reserves))

    # Build reference prices from npc_demand config
    npc_demand_config = settings.npc_demand
    demand_entries = npc_demand_config.get("npc_demand", [])
    ref_prices: dict[str, Decimal] = {}
    for entry in demand_entries:
        good = entry.get("good")
        ref_price = entry.get("reference_price")
        if good and ref_price is not None:
            ref_prices[good] = Decimal(str(ref_price))

    # Place buy orders for tier 1 and tier 2 goods with NPC demand config
    orders_placed = 0
    total_locked = Decimal("0")

    # Scale order size with active agent count
    from sqlalchemy import func

    active_count_result = await db.execute(
        select(func.count(Agent.id)).where(Agent.is_active == True)  # noqa: E712
    )
    active_agents = active_count_result.scalar_one() or 0
    base_qty = max(10, active_agents * 2)

    for good_slug, ref_price in sorted(ref_prices.items()):
        # Determine order quantity based on demand
        qty = base_qty
        cost = ref_price * qty

        # Check bank can afford
        if total_locked + cost > bank_reserves * Decimal("0.3"):
            # Don't use more than 30% of reserves for standing orders
            break

        order = MarketOrder(
            agent_id=NPC_BUYER_SENTINEL,
            good_slug=good_slug,
            side="buy",
            price=float(ref_price),
            quantity_total=qty,
            quantity_filled=0,
            status="open",
        )
        db.add(order)
        total_locked += cost
        orders_placed += 1

    await db.flush()

    logger.info(
        "NPC buy orders: placed %d orders, total locked %.2f",
        orders_placed,
        float(total_locked),
    )

    return {
        "type": "npc_buy_orders",
        "orders_placed": orders_placed,
        "total_locked": float(total_locked),
    }


def _empty_result(now):
    return {
        "type": "npc_marketplace",
        "timestamp": now.isoformat(),
        "total_fills": 0,
        "total_spent": 0.0,
        "purchases": [],
    }
