"""
Order book domain logic for Agent Economy.

Implements a continuous double auction (CDA) order book:
  - Limit orders with price-time priority matching
  - Market orders (buy at very high price / sell at price=0)
  - Partial fills: one order can match against multiple counter-orders
  - Matching always executes at the SELL price (seller gets their ask)
  - Excess funds locked by buy orders are refunded when filled at lower price

Key design decisions:
  - Sell orders lock GOODS from inventory at placement time
  - Buy orders lock FUNDS from balance at placement time
  - Goods/funds are released only via fill, cancel, or bankruptcy liquidation
  - Transaction type="marketplace" — visible to tax authority
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.agents.inventory import add_to_inventory, remove_from_inventory
from backend.marketplace.locking import lock_agents_in_order, lock_market_good
from backend.models.marketplace import MarketOrder

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent

# --- Re-exports so existing imports continue to work ---
from backend.marketplace.browsing import browse_orders, cancel_agent_orders  # noqa: F401
from backend.marketplace.matching import CANCEL_FEE_RATE, match_orders

logger = logging.getLogger(__name__)

# A price high enough that a market buy will cross any reasonable ask
MARKET_BUY_PRICE = Decimal("999999999.99")
# Market sell price: 0 — will cross any reasonable bid
MARKET_SELL_PRICE = Decimal("0.00")


async def place_order(
    db: AsyncSession,
    agent: Agent,
    good_slug: str,
    side: str,
    quantity: int,
    price: Decimal,
    clock: Clock,
    settings: Settings,
    redis: aioredis.Redis | None = None,
) -> dict:
    """
    Place a limit order on the order book.

    For SELL orders:
      - Verify agent has enough goods in inventory
      - Remove goods from inventory (locked into the order)

    For BUY orders:
      - Verify agent has enough balance
      - Deduct funds from balance (locked into the order)

    Then attempt immediate matching.

    Args:
        db:        Active async database session.
        agent:     The agent placing the order.
        good_slug: The good to trade.
        side:      "buy" or "sell".
        quantity:  Number of units to trade.
        price:     Limit price per unit.
        clock:     Clock for timestamps.
        settings:  Application settings.

    Returns:
        Dict with order details and any immediate fills.

    Raises:
        ValueError: If validation fails (insufficient funds/goods, bad params).
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"Invalid side {side!r} — must be 'buy' or 'sell'")
    if quantity <= 0:
        raise ValueError("Quantity must be positive")
    if price <= 0:
        raise ValueError("Price must be greater than zero")
    if quantity > settings.economy.marketplace_order_max_quantity:
        raise ValueError(f"Quantity {quantity} exceeds maximum of {settings.economy.marketplace_order_max_quantity}")

    # Validate the good exists
    goods_config = {g["slug"]: g for g in settings.goods}
    if good_slug not in goods_config:
        raise ValueError(f"Unknown good: {good_slug!r}")

    # Serialize same-good order book mutations so immediate matching and
    # cancellation cannot interleave into opposite agent lock orders.
    await lock_market_good(db, good_slug)

    # Enforce per-agent open order limit to prevent order book flooding
    max_orders = getattr(settings.economy, "marketplace_max_orders_per_agent", 20)
    open_count_result = await db.execute(
        select(MarketOrder).where(
            MarketOrder.agent_id == agent.id,
            MarketOrder.status.in_(["open", "partially_filled"]),
        )
    )
    open_count = len(list(open_count_result.scalars().all()))
    if open_count >= max_orders:
        raise ValueError(
            f"You have {open_count} open orders (max {max_orders}). Cancel some orders before placing new ones."
        )

    # Lock the placing agent before any inventory or balance mutation. This
    # keeps marketplace writes aligned with the agent-first lock order used by
    # other balance-affecting flows like work() and trade responses.
    agent = (await lock_agents_in_order(db, [agent.id]))[agent.id]

    if side == "sell":
        # Lock goods: remove from inventory into the order
        try:
            await remove_from_inventory(db, "agent", agent.id, good_slug, quantity)
        except ValueError as e:
            raise ValueError(f"Cannot place sell order: {e}") from e

    else:  # buy
        # For market buy orders (price = MARKET_BUY_PRICE), we can't lock at
        # the astronomical market price. Instead, find the best available ask
        # to determine the real locking cost. If no asks exist, use the market
        # price as the limit but cap the lock at the agent's full balance.
        effective_price = price
        if price >= MARKET_BUY_PRICE:
            # Market buy: find the worst-case fill price from existing sell orders
            # (we need qty units; find the most expensive we'd buy at)
            sell_result = await db.execute(
                select(MarketOrder)
                .where(
                    MarketOrder.good_slug == good_slug,
                    MarketOrder.side == "sell",
                    MarketOrder.status.in_(["open", "partially_filled"]),
                )
                .order_by(MarketOrder.price.asc())
            )
            sell_orders_list = list(sell_result.scalars().all())

            if not sell_orders_list:
                raise ValueError(
                    f"No sell orders available for {good_slug!r}. Cannot place a market buy order with no sellers."
                )

            # Calculate actual max cost: fill qty units at available prices
            remaining_qty = quantity
            max_cost = Decimal("0")
            worst_price = Decimal("0")
            for so in sell_orders_list:
                avail = so.quantity_total - so.quantity_filled
                take = min(avail, remaining_qty)
                so_price = Decimal(str(so.price))
                max_cost += so_price * take
                worst_price = max(worst_price, so_price)
                remaining_qty -= take
                if remaining_qty <= 0:
                    break

            if remaining_qty > 0:
                raise ValueError(
                    f"Insufficient sell orders for a market buy of {quantity}x {good_slug!r}. "
                    f"Only {quantity - remaining_qty} available."
                )

            # Lock the calculated max cost (not the astronomical market price)
            effective_price = worst_price
            total_cost = max_cost
        else:
            total_cost = price * quantity

        agent_balance = Decimal(str(agent.balance))
        if agent_balance < total_cost:
            raise ValueError(
                f"Insufficient balance. Order requires {float(total_cost):.2f} "
                f"(qty {quantity} × price {float(effective_price):.2f}) but balance is "
                f"{float(agent_balance):.2f}"
            )
        # For market orders, store the effective lock price (not MARKET_BUY_PRICE)
        # so refunds work correctly
        if price >= MARKET_BUY_PRICE:
            price = effective_price
        agent.balance = agent_balance - total_cost
        await db.flush()

    # Create the order
    order = MarketOrder(
        agent_id=agent.id,
        good_slug=good_slug,
        side=side,
        quantity_total=quantity,
        quantity_filled=0,
        price=price,
        status="open",
    )
    db.add(order)
    await db.flush()

    logger.info(
        "Order placed: %s %dx %s @ %.2f by agent %s",
        side,
        quantity,
        good_slug,
        float(price),
        agent.name,
    )

    # Attempt immediate matching
    match_results = await match_orders(db, good_slug, clock, settings, redis=redis)

    # Refresh order state after matching
    await db.refresh(order)

    return {
        "order": order.to_dict(),
        "immediate_fills": match_results["trades_executed"],
    }


async def cancel_order(
    db: AsyncSession,
    agent: Agent,
    order_id: str,
    settings: Settings,
) -> dict:
    """
    Cancel an open or partially-filled order.

    Returns locked goods (sell orders) or locked funds (buy orders) to agent.

    Args:
        db:       Active async database session.
        agent:    The agent who owns the order.
        order_id: UUID string of the order to cancel.
        settings: Application settings.

    Returns:
        Dict confirming cancellation with refund details.

    Raises:
        ValueError: If order not found, not owned by agent, or not cancellable.
    """
    import uuid as _uuid

    try:
        order_uuid = _uuid.UUID(order_id)
    except ValueError:
        raise ValueError(f"Invalid order ID: {order_id!r}")

    preview_result = await db.execute(select(MarketOrder).where(MarketOrder.id == order_uuid))
    preview_order = preview_result.scalar_one_or_none()

    if preview_order is None:
        raise ValueError(f"Order {order_id!r} not found")

    await lock_market_good(db, preview_order.good_slug)

    result = await db.execute(select(MarketOrder).where(MarketOrder.id == order_uuid).with_for_update())
    order = result.scalar_one_or_none()

    if order is None:
        raise ValueError(f"Order {order_id!r} not found")

    if order.agent_id != agent.id:
        raise ValueError("You can only cancel your own orders")

    if order.status not in ("open", "partially_filled"):
        raise ValueError(f"Order cannot be cancelled — current status is {order.status!r}")

    # Calculate how much to return
    unfilled_qty = order.quantity_total - order.quantity_filled

    agent = (await lock_agents_in_order(db, [agent.id]))[agent.id]

    # Calculate 2% cancellation fee to discourage order spoofing
    locked_value = Decimal(str(order.price)) * unfilled_qty
    cancel_fee = (locked_value * CANCEL_FEE_RATE).quantize(Decimal("0.01"))
    # Enforce minimum cancel fee of $0.01 to prevent free spoofing on tiny orders
    if locked_value > 0:
        cancel_fee = max(cancel_fee, Decimal("0.01"))

    if order.side == "sell":
        # Return unsold goods to inventory
        try:
            await add_to_inventory(db, "agent", agent.id, order.good_slug, unfilled_qty, settings)
        except ValueError:
            raise ValueError(
                f"Cannot cancel: returning {unfilled_qty}x {order.good_slug} would exceed your "
                f"storage capacity. Free up space first by discarding goods via "
                f"POST /v1/inventory/discard, then retry the cancel."
            )
        # Deduct monetary fee from agent balance (cap at available balance to avoid negative)
        agent_bal = Decimal(str(agent.balance))
        cancel_fee = min(cancel_fee, max(agent_bal, Decimal("0")))
        agent.balance = agent_bal - cancel_fee
        refund_info = {
            "type": "goods_returned",
            "good_slug": order.good_slug,
            "quantity": unfilled_qty,
            "cancel_fee": float(cancel_fee),
        }
    else:  # buy
        # Return locked funds minus 2% cancellation fee
        locked_funds = locked_value
        refund_amount = locked_funds - cancel_fee
        agent.balance = Decimal(str(agent.balance)) + refund_amount
        refund_info = {
            "type": "funds_returned",
            "amount": float(refund_amount),
            "cancel_fee": float(cancel_fee),
        }

    order.status = "cancelled"
    await db.flush()

    logger.info(
        "Order %s cancelled by agent %s — returned: %s",
        order_id,
        agent.name,
        refund_info,
    )

    return {
        "cancelled": True,
        "order_id": order_id,
        "refund": refund_info,
    }
