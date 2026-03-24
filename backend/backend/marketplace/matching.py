"""
Order matching engine for Agent Economy.

Implements a continuous double auction (CDA) matching algorithm:
  - Price-time priority: best buy (highest) matches best sell (lowest)
  - Execution always at the SELL price (seller gets their ask)
  - Partial fills: one order can match against multiple counter-orders
  - Refunds excess locked funds when buy fills at price < buy limit
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.agents.inventory import add_to_inventory
from backend.models.agent import Agent
from backend.models.marketplace import MarketOrder, MarketTrade
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

CANCEL_FEE_RATE = Decimal("0.02")  # 2% cancellation fee to prevent spoofing


async def match_orders(
    db: AsyncSession,
    good_slug: str,
    clock: Clock,
    settings: Settings,
    redis: aioredis.Redis | None = None,
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
    trade_details: list[dict] = []

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
        .with_for_update()
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
        .with_for_update()
    )
    sell_orders = list(sell_result.scalars().all())

    if not buy_orders or not sell_orders:
        return {"trades_executed": 0, "total_volume": 0, "trade_details": []}

    buy_idx = 0
    sell_idx = 0

    while buy_idx < len(buy_orders) and sell_idx < len(sell_orders):
        buy_order = buy_orders[buy_idx]
        sell_order = sell_orders[sell_idx]

        # Prevent self-trading (wash trading)
        if buy_order.agent_id == sell_order.agent_id:
            # Skip: same agent on both sides. Try next sell order.
            sell_idx += 1
            continue

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

        # --- Load buyer and seller agents (locked to prevent concurrent balance changes) ---
        buyer_result = await db.execute(select(Agent).where(Agent.id == buy_order.agent_id).with_for_update())
        buyer = buyer_result.scalar_one_or_none()

        seller_result = await db.execute(select(Agent).where(Agent.id == sell_order.agent_id).with_for_update())
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
            # Buyer storage full — auto-cancel the buy order and refund locked funds
            # (minus the standard cancellation fee) to prevent phantom orders
            logger.warning(
                "Cannot deliver %dx %s to buyer %s: %s — auto-cancelling buy order",
                fill_qty,
                good_slug,
                buyer.name,
                e,
            )
            unfilled_qty = buy_order.quantity_total - buy_order.quantity_filled
            locked_value = Decimal(str(buy_order.price)) * unfilled_qty
            cancel_fee = (locked_value * CANCEL_FEE_RATE).quantize(Decimal("0.01"))
            if locked_value > 0:
                cancel_fee = max(cancel_fee, Decimal("0.01"))
            refund_amount = locked_value - cancel_fee
            buyer.balance = Decimal(str(buyer.balance)) + refund_amount
            buy_order.status = "cancelled"
            await db.flush()
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

        # --- Record transaction ---
        # One canonical transaction per fill: buyer pays seller.
        # from=buyer, to=seller, amount=payment, type="marketplace"
        txn = Transaction(
            type="marketplace",
            from_agent_id=buyer.id,
            to_agent_id=seller.id,
            amount=payment,
            metadata_json={
                "good_slug": good_slug,
                "quantity": fill_qty,
                "price_per_unit": float(exec_price),
                "buy_order_id": str(buy_order.id),
                "sell_order_id": str(sell_order.id),
            },
        )
        db.add(txn)

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
        trade_detail = {
            "buyer_name": buyer.name,
            "seller_name": seller.name,
            "good_slug": good_slug,
            "quantity": fill_qty,
            "price": float(exec_price),
            "total_value": float(exec_price * fill_qty),
        }
        trade_details.append(trade_detail)

        # Emit spectator event for each fill
        if redis is not None:
            try:
                from backend.spectator.events import emit_spectator_event

                drama = "notable" if trade_detail["total_value"] > 50 else "routine"
                await emit_spectator_event(redis, "marketplace_fill", trade_detail, clock, drama)
            except Exception:
                pass

        logger.info(
            "Trade executed: %dx %s @ %.2f (buyer: %s, seller: %s)",
            fill_qty,
            good_slug,
            float(exec_price),
            buyer.name,
            seller.name,
        )

    return {"trades_executed": trades_executed, "total_volume": total_volume, "trade_details": trade_details}
