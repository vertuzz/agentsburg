"""
Order book browsing and bulk cancellation for Agent Economy.

Provides read-only views into the order book:
  - browse_orders: paginated order book depth + recent trades
  - cancel_agent_orders: bulk cancel for bankruptcy liquidation
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.inventory import add_to_inventory
from backend.models.agent import Agent
from backend.models.marketplace import MarketOrder, MarketTrade

if TYPE_CHECKING:
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def browse_orders(
    db: AsyncSession,
    good_slug: str | None,
    page: int,
    page_size: int,
    settings: Settings,
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
        select(MarketTrade).where(MarketTrade.good_slug == good_slug).order_by(MarketTrade.executed_at.desc()).limit(50)
    )
    recent_trades = [t.to_dict() for t in trades_result.scalars().all()]

    # Best bid/ask spread
    best_bid = max(buy_book.keys()) if buy_book else None
    best_ask = min(sell_book.keys()) if sell_book else None

    return {
        "good_slug": good_slug,
        "bids": [{"price": p, "quantity": q} for p, q in sorted(buy_book.items(), reverse=True)],
        "asks": [{"price": p, "quantity": q} for p, q in sorted(sell_book.items())],
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
        select(MarketOrder.good_slug, MarketOrder.price, MarketOrder.quantity_total, MarketOrder.quantity_filled)
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
        select(MarketOrder.good_slug, MarketOrder.price, MarketOrder.quantity_total, MarketOrder.quantity_filled).where(
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
        select(MarketOrder.good_slug, MarketOrder.price, MarketOrder.quantity_total, MarketOrder.quantity_filled).where(
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
    paginated = all_slugs[offset : offset + page_size]

    summary = []
    for slug in paginated:
        d = goods_data[slug]
        summary.append(
            {
                "good_slug": slug,
                "buy_volume": d.get("buy_volume", 0),
                "sell_volume": d.get("sell_volume", 0),
                "min_ask": d.get("min_ask"),
                "max_bid": d.get("max_bid"),
                "last_price": last_prices.get(slug),
            }
        )

    return {
        "goods": summary,
        "total": len(all_slugs),
        "page": page,
        "page_size": page_size,
    }


async def cancel_agent_orders(
    db: AsyncSession,
    agent: Agent,
    settings: Settings,
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
