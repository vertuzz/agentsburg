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
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.inventory import add_to_inventory, remove_from_inventory
from backend.models.agent import Agent
from backend.models.marketplace import MarketOrder, MarketTrade
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from datetime import datetime

    from backend.clock import Clock
    from backend.config import Settings

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
    clock: "Clock",
    settings: "Settings",
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
    if price < 0:
        raise ValueError("Price cannot be negative")
    if quantity > settings.economy.marketplace_order_max_quantity:
        raise ValueError(
            f"Quantity {quantity} exceeds maximum of "
            f"{settings.economy.marketplace_order_max_quantity}"
        )

    # Validate the good exists
    goods_config = {g["slug"]: g for g in settings.goods}
    if good_slug not in goods_config:
        raise ValueError(f"Unknown good: {good_slug!r}")

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
                    f"No sell orders available for {good_slug!r}. "
                    "Cannot place a market buy order with no sellers."
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
    match_results = await match_orders(db, good_slug, clock, settings)

    # Refresh order state after matching
    await db.refresh(order)

    return {
        "order": order.to_dict(),
        "immediate_fills": match_results["trades_executed"],
    }


async def match_orders(
    db: AsyncSession,
    good_slug: str,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Run the matching engine for a specific good.

    Implements price-time priority:
    - Best buy (highest price) matches against best sell (lowest price)
    - When prices cross (buy price >= sell price), a trade executes at SELL price
    - Handles partial fills: one order can match against multiple counter-orders
    - Refunds excess locked funds when buy fills at price < buy limit

    Args:
        db:        Active async database session.
        good_slug: The good to match orders for.
        clock:     Clock for trade timestamps.
        settings:  Application settings.

    Returns:
        Dict with count of trades executed and total volume.
    """
    now = clock.now()
    trades_executed = 0
    total_volume = 0

    # Load all open buy orders for this good, sorted by price DESC then created_at ASC
    # (highest bidder first; earliest order breaks ties)
    buy_result = await db.execute(
        select(MarketOrder)
        .where(
            MarketOrder.good_slug == good_slug,
            MarketOrder.side == "buy",
            MarketOrder.status.in_(["open", "partially_filled"]),
        )
        .order_by(MarketOrder.price.desc(), MarketOrder.created_at.asc())
    )
    buy_orders = list(buy_result.scalars().all())

    # Load all open sell orders sorted by price ASC then created_at ASC
    # (lowest asker first; earliest order breaks ties)
    sell_result = await db.execute(
        select(MarketOrder)
        .where(
            MarketOrder.good_slug == good_slug,
            MarketOrder.side == "sell",
            MarketOrder.status.in_(["open", "partially_filled"]),
        )
        .order_by(MarketOrder.price.asc(), MarketOrder.created_at.asc())
    )
    sell_orders = list(sell_result.scalars().all())

    if not buy_orders or not sell_orders:
        return {"trades_executed": 0, "total_volume": 0}

    buy_idx = 0
    sell_idx = 0

    while buy_idx < len(buy_orders) and sell_idx < len(sell_orders):
        buy_order = buy_orders[buy_idx]
        sell_order = sell_orders[sell_idx]

        buy_price = Decimal(str(buy_order.price))
        sell_price = Decimal(str(sell_order.price))

        # No more matches possible once best buy < best sell
        if buy_price < sell_price:
            break

        # Execution price is the SELL price (seller gets their ask)
        exec_price = sell_price

        buy_remaining = buy_order.quantity_total - buy_order.quantity_filled
        sell_remaining = sell_order.quantity_total - sell_order.quantity_filled

        # Match as much as possible
        fill_qty = min(buy_remaining, sell_remaining)

        # --- Load buyer and seller agents ---
        buyer_result = await db.execute(
            select(Agent).where(Agent.id == buy_order.agent_id)
        )
        buyer = buyer_result.scalar_one_or_none()

        seller_result = await db.execute(
            select(Agent).where(Agent.id == sell_order.agent_id)
        )
        seller = seller_result.scalar_one_or_none()

        if buyer is None or seller is None:
            # Agent deleted — skip this order pair
            logger.warning(
                "Match skipped: buyer or seller agent missing for order pair %s / %s",
                buy_order.id,
                sell_order.id,
            )
            if buyer is None:
                buy_order.status = "cancelled"
                buy_idx += 1
            if seller is None:
                sell_order.status = "cancelled"
                sell_idx += 1
            continue

        # --- Transfer goods to buyer ---
        # Seller's goods were already removed from inventory when the sell order was placed.
        # Now add them to the buyer's inventory.
        try:
            await add_to_inventory(db, "agent", buyer.id, good_slug, fill_qty, settings)
        except ValueError as e:
            # Buyer storage full — cannot complete this fill
            # Skip this buy order and try next
            logger.warning(
                "Cannot deliver %dx %s to buyer %s: %s — skipping buy order",
                fill_qty,
                good_slug,
                buyer.name,
                e,
            )
            buy_idx += 1
            continue

        # --- Transfer payment to seller ---
        # Total payment at sell price
        payment = exec_price * fill_qty

        # Refund excess: buyer locked funds at their limit price, but fill at lower sell price
        locked_per_unit = Decimal(str(buy_order.price))
        refund_per_unit = locked_per_unit - exec_price
        if refund_per_unit > 0:
            refund_amount = refund_per_unit * fill_qty
            buyer.balance = Decimal(str(buyer.balance)) + refund_amount
            logger.debug(
                "Refunding %.2f to buyer %s (bought at %.2f, locked at %.2f)",
                float(refund_amount),
                buyer.name,
                float(exec_price),
                float(locked_per_unit),
            )

        # Credit seller
        seller.balance = Decimal(str(seller.balance)) + payment

        # --- Record the trade ---
        trade_record = MarketTrade(
            buy_order_id=buy_order.id,
            sell_order_id=sell_order.id,
            good_slug=good_slug,
            quantity=fill_qty,
            price=exec_price,
            executed_at=now,
        )
        db.add(trade_record)

        # --- Record transactions ---
        # Buyer paid (funds were locked at placement, now settled at exec_price)
        buyer_payment = exec_price * fill_qty
        txn_buyer = Transaction(
            type="marketplace",
            from_agent_id=buyer.id,
            to_agent_id=seller.id,
            amount=buyer_payment,
            metadata_json={
                "good_slug": good_slug,
                "quantity": fill_qty,
                "price_per_unit": float(exec_price),
                "side": "buy",
                "order_id": str(buy_order.id),
            },
        )
        db.add(txn_buyer)

        # Seller received
        txn_seller = Transaction(
            type="marketplace",
            from_agent_id=buyer.id,
            to_agent_id=seller.id,
            amount=payment,
            metadata_json={
                "good_slug": good_slug,
                "quantity": fill_qty,
                "price_per_unit": float(exec_price),
                "side": "sell",
                "order_id": str(sell_order.id),
            },
        )
        db.add(txn_seller)

        # --- Update order quantities and statuses ---
        buy_order.quantity_filled += fill_qty
        sell_order.quantity_filled += fill_qty

        if buy_order.quantity_filled >= buy_order.quantity_total:
            buy_order.status = "filled"
            buy_idx += 1
        else:
            buy_order.status = "partially_filled"

        if sell_order.quantity_filled >= sell_order.quantity_total:
            sell_order.status = "filled"
            sell_idx += 1
        else:
            sell_order.status = "partially_filled"

        await db.flush()

        trades_executed += 1
        total_volume += fill_qty

        logger.info(
            "Trade executed: %dx %s @ %.2f (buyer: %s, seller: %s)",
            fill_qty,
            good_slug,
            float(exec_price),
            buyer.name,
            seller.name,
        )

    return {"trades_executed": trades_executed, "total_volume": total_volume}


async def cancel_order(
    db: AsyncSession,
    agent: Agent,
    order_id: str,
    settings: "Settings",
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

    result = await db.execute(
        select(MarketOrder).where(MarketOrder.id == order_uuid)
    )
    order = result.scalar_one_or_none()

    if order is None:
        raise ValueError(f"Order {order_id!r} not found")

    if order.agent_id != agent.id:
        raise ValueError("You can only cancel your own orders")

    if order.status not in ("open", "partially_filled"):
        raise ValueError(
            f"Order cannot be cancelled — current status is {order.status!r}"
        )

    # Calculate how much to return
    unfilled_qty = order.quantity_total - order.quantity_filled

    if order.side == "sell":
        # Return unsold goods to inventory
        await add_to_inventory(db, "agent", agent.id, order.good_slug, unfilled_qty, settings)
        refund_info = {
            "type": "goods_returned",
            "good_slug": order.good_slug,
            "quantity": unfilled_qty,
        }
    else:  # buy
        # Return locked funds (at order price per unit, for unfilled portion)
        locked_funds = Decimal(str(order.price)) * unfilled_qty
        agent.balance = Decimal(str(agent.balance)) + locked_funds
        refund_info = {
            "type": "funds_returned",
            "amount": float(locked_funds),
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


async def browse_orders(
    db: AsyncSession,
    good_slug: str | None,
    page: int,
    page_size: int,
    settings: "Settings",
) -> dict:
    """
    Browse the order book and recent price history.

    If good_slug is provided: return that good's order book depth + recent trades.
    If good_slug is None: return a summary of all goods with active orders.

    Args:
        db:        Active async database session.
        good_slug: Good to browse (None for summary).
        page:      Page number (1-indexed).
        page_size: Results per page.
        settings:  Application settings.

    Returns:
        Dict with order book data.
    """
    if good_slug is not None:
        return await _browse_single_good(db, good_slug, page, page_size)
    else:
        return await _browse_all_goods(db, page, page_size)


async def _browse_single_good(
    db: AsyncSession,
    good_slug: str,
    page: int,
    page_size: int,
) -> dict:
    """Return order book depth and recent trades for a specific good."""
    # Aggregate buy orders by price (descending)
    buy_result = await db.execute(
        select(MarketOrder)
        .where(
            MarketOrder.good_slug == good_slug,
            MarketOrder.side == "buy",
            MarketOrder.status.in_(["open", "partially_filled"]),
        )
        .order_by(MarketOrder.price.desc(), MarketOrder.created_at.asc())
    )
    buy_orders = list(buy_result.scalars().all())

    sell_result = await db.execute(
        select(MarketOrder)
        .where(
            MarketOrder.good_slug == good_slug,
            MarketOrder.side == "sell",
            MarketOrder.status.in_(["open", "partially_filled"]),
        )
        .order_by(MarketOrder.price.asc(), MarketOrder.created_at.asc())
    )
    sell_orders = list(sell_result.scalars().all())

    # Aggregate into price levels
    buy_book: dict[float, int] = {}
    for o in buy_orders:
        p = float(o.price)
        buy_book[p] = buy_book.get(p, 0) + o.quantity_remaining

    sell_book: dict[float, int] = {}
    for o in sell_orders:
        p = float(o.price)
        sell_book[p] = sell_book.get(p, 0) + o.quantity_remaining

    # Recent trades (last 50)
    trades_result = await db.execute(
        select(MarketTrade)
        .where(MarketTrade.good_slug == good_slug)
        .order_by(MarketTrade.executed_at.desc())
        .limit(50)
    )
    recent_trades = [t.to_dict() for t in trades_result.scalars().all()]

    # Best bid/ask spread
    best_bid = max(buy_book.keys()) if buy_book else None
    best_ask = min(sell_book.keys()) if sell_book else None

    return {
        "good_slug": good_slug,
        "bids": [
            {"price": p, "quantity": q}
            for p, q in sorted(buy_book.items(), reverse=True)
        ],
        "asks": [
            {"price": p, "quantity": q}
            for p, q in sorted(sell_book.items())
        ],
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": float(best_ask - best_bid) if best_bid and best_ask else None,
        "recent_trades": recent_trades,
    }


async def _browse_all_goods(
    db: AsyncSession,
    page: int,
    page_size: int,
) -> dict:
    """Return summary of all goods with active order book activity."""
    # Get distinct goods with open orders
    buy_result = await db.execute(
        select(MarketOrder.good_slug, MarketOrder.price, MarketOrder.quantity_total,
               MarketOrder.quantity_filled)
        .where(MarketOrder.status.in_(["open", "partially_filled"]))
        .order_by(MarketOrder.good_slug)
    )
    all_orders = list(buy_result.all())

    # Group by good_slug
    goods_data: dict[str, dict] = {}
    for row in all_orders:
        slug = row.good_slug
        if slug not in goods_data:
            goods_data[slug] = {"buy_volume": 0, "sell_volume": 0, "min_ask": None, "max_bid": None}

    # Get min asks and max bids
    sell_orders_res = await db.execute(
        select(MarketOrder.good_slug, MarketOrder.price, MarketOrder.quantity_total,
               MarketOrder.quantity_filled)
        .where(
            MarketOrder.status.in_(["open", "partially_filled"]),
            MarketOrder.side == "sell",
        )
    )
    for row in sell_orders_res.all():
        slug = row.good_slug
        if slug not in goods_data:
            goods_data[slug] = {"buy_volume": 0, "sell_volume": 0, "min_ask": None, "max_bid": None}
        remaining = row.quantity_total - row.quantity_filled
        goods_data[slug]["sell_volume"] = goods_data[slug].get("sell_volume", 0) + remaining
        p = float(row.price)
        if goods_data[slug]["min_ask"] is None or p < goods_data[slug]["min_ask"]:
            goods_data[slug]["min_ask"] = p

    buy_orders_res = await db.execute(
        select(MarketOrder.good_slug, MarketOrder.price, MarketOrder.quantity_total,
               MarketOrder.quantity_filled)
        .where(
            MarketOrder.status.in_(["open", "partially_filled"]),
            MarketOrder.side == "buy",
        )
    )
    for row in buy_orders_res.all():
        slug = row.good_slug
        if slug not in goods_data:
            goods_data[slug] = {"buy_volume": 0, "sell_volume": 0, "min_ask": None, "max_bid": None}
        remaining = row.quantity_total - row.quantity_filled
        goods_data[slug]["buy_volume"] = goods_data[slug].get("buy_volume", 0) + remaining
        p = float(row.price)
        if goods_data[slug]["max_bid"] is None or p > goods_data[slug]["max_bid"]:
            goods_data[slug]["max_bid"] = p

    # Get last trade price for each good
    trades_res = await db.execute(
        select(MarketTrade.good_slug, MarketTrade.price, MarketTrade.executed_at)
        .order_by(MarketTrade.executed_at.desc())
        .limit(500)
    )
    last_prices: dict[str, float] = {}
    for row in trades_res.all():
        if row.good_slug not in last_prices:
            last_prices[row.good_slug] = float(row.price)

    all_slugs = sorted(goods_data.keys())
    offset = (page - 1) * page_size
    paginated = all_slugs[offset: offset + page_size]

    summary = []
    for slug in paginated:
        d = goods_data[slug]
        summary.append({
            "good_slug": slug,
            "buy_volume": d.get("buy_volume", 0),
            "sell_volume": d.get("sell_volume", 0),
            "min_ask": d.get("min_ask"),
            "max_bid": d.get("max_bid"),
            "last_price": last_prices.get(slug),
        })

    return {
        "goods": summary,
        "total": len(all_slugs),
        "page": page,
        "page_size": page_size,
    }


async def cancel_agent_orders(
    db: AsyncSession,
    agent: Agent,
    settings: "Settings",
) -> int:
    """
    Cancel all open/partially-filled orders for an agent.

    Used during bankruptcy. Returns locked goods/funds.

    Args:
        db:      Active async database session.
        agent:   The agent whose orders to cancel.
        settings: Application settings.

    Returns:
        Count of cancelled orders.
    """
    result = await db.execute(
        select(MarketOrder).where(
            MarketOrder.agent_id == agent.id,
            MarketOrder.status.in_(["open", "partially_filled"]),
        )
    )
    orders = list(result.scalars().all())
    cancelled_count = 0

    for order in orders:
        unfilled_qty = order.quantity_total - order.quantity_filled
        if unfilled_qty <= 0:
            order.status = "cancelled"
            cancelled_count += 1
            continue

        if order.side == "sell":
            # Return goods to inventory (skip storage check for bankruptcy)
            try:
                await add_to_inventory(db, "agent", agent.id, order.good_slug, unfilled_qty, settings)
            except ValueError:
                # If storage is full during bankruptcy, just zero out and discard
                # The inventory liquidation step will handle the rest
                pass
        else:  # buy
            locked_funds = Decimal(str(order.price)) * unfilled_qty
            agent.balance = Decimal(str(agent.balance)) + locked_funds

        order.status = "cancelled"
        cancelled_count += 1

    if cancelled_count:
        await db.flush()

    logger.info(
        "Cancelled %d orders for agent %s (bankruptcy)",
        cancelled_count,
        agent.name,
    )

    return cancelled_count
