"""
Fast tick processing for Agent Economy.

The fast tick runs every minute (60 seconds). It handles time-sensitive
operations that need frequent processing:
- NPC storefront purchases (aggregate, price-weighted distribution)
- Marketplace order matching for all goods with open orders
- Trade escrow expiry
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.marketplace.orderbook import match_orders
from backend.marketplace.trading import expire_trades
from backend.models.marketplace import MarketOrder

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def run_fast_tick(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
    redis: aioredis.Redis | None = None,
) -> dict:
    """
    Run all fast tick processing.

    Order:
      1. NPC storefront purchases — NPCs walk in and buy from storefronts
      2. Match pending marketplace orders for all active goods
      3. Expire overdue trade escrows

    Args:
        db:       Active async database session.
        clock:    Clock for time-dependent logic.
        settings: Application settings.

    Returns:
        Dict summarizing what was processed.
    """
    now = clock.now()
    logger.debug("Fast tick at %s", now.isoformat())

    processed = []

    # --- NPC storefront purchases ---
    from backend.economy.npc_consumers import simulate_npc_purchases

    npc_result = await simulate_npc_purchases(db, clock, settings)
    processed.append(
        {
            "type": "npc_purchases",
            "transactions": npc_result["total_transactions"],
            "revenue": npc_result["total_revenue"],
        }
    )

    # --- NPC marketplace demand (bank buys raw goods from sell orders) ---
    from backend.economy.npc_marketplace import place_npc_buy_orders, simulate_npc_marketplace_demand

    npc_mkt_result = await simulate_npc_marketplace_demand(db, clock, settings)
    processed.append(
        {
            "type": "npc_marketplace",
            "fills": npc_mkt_result["total_fills"],
            "spent": npc_mkt_result["total_spent"],
        }
    )

    # --- NPC buy orders (visible demand on the marketplace) ---
    npc_buy_result = await place_npc_buy_orders(db, clock, settings)
    processed.append(
        {
            "type": "npc_buy_orders",
            "orders_placed": npc_buy_result["orders_placed"],
        }
    )

    # --- Marketplace order matching ---
    matching_result = await _run_order_matching(db, clock, settings, redis=redis)
    processed.append(matching_result)

    # --- Trade escrow expiry ---
    expiry_result = await expire_trades(db, clock, settings)
    processed.append(
        {
            "type": "trade_expiry",
            "expired": expiry_result["expired"],
        }
    )

    return {
        "tick_type": "fast",
        "timestamp": now.isoformat(),
        "processed": processed,
    }


async def _run_order_matching(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
    redis: aioredis.Redis | None = None,
) -> dict:
    """
    Run the matching engine for all goods that have open orders.

    Queries the set of distinct good_slugs with open/partially-filled orders
    and calls match_orders() for each.

    Returns:
        Summary dict with goods processed and total trades executed.
    """
    # Find all goods with open orders
    result = await db.execute(
        select(MarketOrder.good_slug).where(MarketOrder.status.in_(["open", "partially_filled"])).distinct()
    )
    active_goods = [row[0] for row in result.all()]

    total_trades = 0
    total_volume = 0
    goods_processed = []
    trade_details: list[dict] = []

    for good_slug in active_goods:
        match_result = await match_orders(db, good_slug, clock, settings, redis=redis)
        if match_result["trades_executed"] > 0:
            total_trades += match_result["trades_executed"]
            total_volume += match_result["total_volume"]
            goods_processed.append(
                {
                    "good_slug": good_slug,
                    "trades": match_result["trades_executed"],
                    "volume": match_result["total_volume"],
                }
            )
            trade_details.extend(match_result.get("trade_details", []))

    logger.info(
        "Order matching: %d goods with open orders, %d trades executed (volume: %d)",
        len(active_goods),
        total_trades,
        total_volume,
    )

    return {
        "type": "order_matching",
        "goods_checked": len(active_goods),
        "trades_executed": total_trades,
        "total_volume": total_volume,
        "goods_with_fills": goods_processed,
        "trade_details": trade_details,
    }
