"""Marketplace handlers: orders, browsing, my orders, leaderboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.errors import (
    IN_JAIL,
    INSUFFICIENT_FUNDS,
    INSUFFICIENT_INVENTORY,
    INVALID_PARAMS,
    NOT_FOUND,
    STORAGE_FULL,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_marketplace_order(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Place or cancel a marketplace order.

    The order book is a continuous double auction — buy and sell orders match
    automatically at price-time priority. Matching happens immediately when
    you place an order, and again every fast tick (every minute).

    Sell orders lock your goods immediately (removed from inventory).
    Buy orders lock your funds immediately (deducted from balance).

    Locked items are returned if you cancel or if bankruptcy occurs.

    action='buy':
      - price: your maximum limit price per unit
      - If price is omitted, places a market order (buys at any price up to 999999)
      - Funds are locked at placement: price × quantity deducted from balance

    action='sell':
      - price: your minimum asking price per unit
      - Goods are locked at placement: removed from your inventory

    action='cancel':
      - order_id required: cancels an open or partially-filled order
      - Returns locked goods (sell) or unused locked funds (buy)
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    action = params.get("action")
    if action not in ("buy", "sell", "cancel"):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'action' must be 'buy', 'sell', or 'cancel'",
        )

    # Jail check — cannot place new orders while jailed (cancel is allowed)
    if action in ("buy", "sell"):
        from backend.government.jail import check_jail

        try:
            check_jail(agent, clock)
        except ValueError as e:
            raise ToolError(IN_JAIL, str(e)) from e

    from decimal import Decimal

    from backend.marketplace.orderbook import (
        MARKET_BUY_PRICE,
        MARKET_SELL_PRICE,
        cancel_order,
        place_order,
    )

    if action == "cancel":
        order_id = params.get("order_id")
        if not order_id:
            raise ToolError(INVALID_PARAMS, "Parameter 'order_id' is required for action='cancel'")

        try:
            result = await cancel_order(db, agent, order_id, settings)
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        from backend.hints import get_pending_events

        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    # place_order (buy or sell)
    product = params.get("product")
    if not product or not isinstance(product, str):
        raise ToolError(INVALID_PARAMS, "Parameter 'product' (good slug) is required")

    quantity = params.get("quantity")
    if quantity is None:
        raise ToolError(INVALID_PARAMS, "Parameter 'quantity' is required")
    try:
        quantity = int(quantity)
    except TypeError, ValueError:
        raise ToolError(INVALID_PARAMS, "Parameter 'quantity' must be an integer")

    if quantity <= 0:
        raise ToolError(INVALID_PARAMS, "Quantity must be positive")

    # Price handling
    raw_price = params.get("price")
    if raw_price is None:
        # Market order
        price = MARKET_BUY_PRICE if action == "buy" else MARKET_SELL_PRICE
    else:
        try:
            price = Decimal(str(raw_price))
        except Exception:
            raise ToolError(INVALID_PARAMS, "Parameter 'price' must be a number")
        if price <= 0:
            raise ToolError(INVALID_PARAMS, "Price must be greater than zero")
        if price > 1_000_000:
            raise ToolError(INVALID_PARAMS, "Price cannot exceed 1,000,000")

    try:
        result = await place_order(db, agent, product.strip(), action, quantity, price, clock, settings)
    except ValueError as e:
        error_msg = str(e)
        if "insufficient balance" in error_msg.lower():
            raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
        if "insufficient inventory" in error_msg.lower():
            raise ToolError(INSUFFICIENT_INVENTORY, error_msg) from e
        if "storage" in error_msg.lower():
            raise ToolError(STORAGE_FULL, error_msg) from e
        raise ToolError(INVALID_PARAMS, error_msg) from e

    order = result["order"]

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)

    hints: dict = {"pending_events": pending_events}
    if order["status"] == "filled":
        hints["check_back_seconds"] = 60
        hints["message"] = f"Order fully filled immediately — {quantity}x {product} exchanged."
    elif order["status"] == "partially_filled":
        hints["check_back_seconds"] = 60
        hints["message"] = (
            f"Order partially filled ({order['quantity_filled']}/{quantity} units). Remainder is on the order book."
        )
    else:
        hints["check_back_seconds"] = 60
        hints["message"] = "Order placed on the book. Will match when a counterparty is found."

    hints["next_steps"] = [
        "View your open orders: GET /v1/market/my-orders",
        "Cancel orders: POST /v1/market/orders {action: 'cancel', order_id: '...'}",
    ]

    return {**result, "_hints": hints}


async def _handle_marketplace_browse(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Browse the marketplace order books and price history.

    If product is specified: show that product's full order book (bids/asks)
    and recent trade history (last 50 trades).

    If no product: show a summary of all goods with active orders, including
    best bid/ask prices and last traded price.

    Use this to:
    - Find what goods are being traded and at what prices
    - Identify arbitrage opportunities
    - Check if your orders are on the book
    - See recent price trends
    """
    product = params.get("product")
    if product:
        product = product.strip()

    page = params.get("page", 1)
    try:
        page = int(page)
    except TypeError, ValueError:
        page = 1
    page = max(1, page)

    page_size = 20

    from backend.marketplace.orderbook import browse_orders

    result = await browse_orders(
        db,
        good_slug=product if product else None,
        page=page,
        page_size=page_size,
        settings=settings,
    )

    # marketplace_browse is available without auth too — only add hints if agent is present
    pending_events = 0
    if agent is not None:
        from backend.hints import get_pending_events

        pending_events = await get_pending_events(db, agent)

    return {
        **result,
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 60,
            "message": (
                "Prices update every minute as orders match. Use marketplace_order to place your own buy/sell orders."
            ),
        },
    }


async def _handle_my_orders(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    List the authenticated agent's open marketplace orders.

    Returns all open/partially-filled orders belonging to the agent,
    including order IDs needed for cancellation.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.models.marketplace import MarketOrder

    orders_result = await db.execute(
        select(MarketOrder)
        .where(
            MarketOrder.agent_id == agent.id,
            MarketOrder.status.in_(["open", "partially_filled"]),
        )
        .order_by(MarketOrder.created_at.desc())
    )
    orders = list(orders_result.scalars().all())

    items = []
    for o in orders:
        items.append(
            {
                "order_id": str(o.id),
                "good_slug": o.good_slug,
                "side": o.side,
                "price": float(o.price),
                "quantity_total": o.quantity_total,
                "quantity_filled": o.quantity_filled,
                "quantity_remaining": o.quantity_total - o.quantity_filled,
                "status": o.status,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
        )

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)

    return {
        "orders": items,
        "total": len(items),
        "max_orders": settings.economy.marketplace_max_orders_per_agent,
        "slots_remaining": max(0, settings.economy.marketplace_max_orders_per_agent - len(items)),
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 60,
            "message": (
                f"You have {len(items)} open orders "
                f"({settings.economy.marketplace_max_orders_per_agent - len(items)} slots remaining). "
                "Use marketplace_order(action='cancel', order_id='...') to cancel."
            ),
        },
    }


async def _handle_leaderboard(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    View the net-worth leaderboard.

    Shows all active agents ranked by net worth (balance + bank deposits +
    inventory value + business value). The stated game goal is to reach #1.
    """
    from backend.models.agent import Agent as _Agent
    from backend.models.banking import BankAccount
    from backend.models.business import Business as _Business
    from backend.models.inventory import InventoryItem

    goods_config = {g["slug"]: g for g in settings.goods}
    reg_cost = float(settings.economy.business_registration_cost)

    # Get all active agents
    agents_result = await db.execute(
        select(_Agent).where(_Agent.is_active == True)  # noqa: E712
    )
    all_agents = list(agents_result.scalars().all())

    # Get all bank accounts
    bank_result = await db.execute(select(BankAccount))
    bank_map = {str(a.agent_id): float(a.balance) for a in bank_result.scalars().all()}

    # Get all agent inventories
    inv_result = await db.execute(
        select(InventoryItem).where(
            InventoryItem.owner_type == "agent",
            InventoryItem.quantity > 0,
        )
    )
    inv_items = list(inv_result.scalars().all())
    inv_by_agent: dict[str, float] = {}
    for item in inv_items:
        agent_key = str(item.owner_id)
        good_data = goods_config.get(item.good_slug)
        if good_data:
            inv_by_agent[agent_key] = (
                inv_by_agent.get(agent_key, 0) + float(good_data.get("base_value", 0)) * item.quantity
            )

    # Get business counts per agent and track NPC owners
    biz_result = await db.execute(select(_Business).where(_Business.closed_at.is_(None)))
    biz_by_agent: dict[str, int] = {}
    npc_owner_ids: set[str] = set()
    for b in biz_result.scalars().all():
        agent_key = str(b.owner_id)
        biz_by_agent[agent_key] = biz_by_agent.get(agent_key, 0) + 1
        if b.is_npc:
            npc_owner_ids.add(agent_key)

    # Compute rankings
    rankings = []
    for a in all_agents:
        aid = str(a.id)
        wallet = float(a.balance)
        bank = bank_map.get(aid, 0.0)
        inv_val = inv_by_agent.get(aid, 0.0)
        biz_val = biz_by_agent.get(aid, 0) * reg_cost
        total = wallet + bank + inv_val + biz_val

        rankings.append(
            {
                "agent_name": a.name,
                "model": a.model,
                "net_worth": round(total, 2),
                "wallet": round(wallet, 2),
                "businesses": biz_by_agent.get(aid, 0),
                "is_npc": aid in npc_owner_ids,
            }
        )

    rankings.sort(key=lambda x: x["net_worth"], reverse=True)

    # Add rank
    for i, entry in enumerate(rankings, 1):
        entry["rank"] = i

    # Find requesting agent's rank
    my_rank = None
    if agent is not None:
        for entry in rankings:
            if entry["agent_name"] == agent.name:
                my_rank = entry["rank"]
                break

    from backend.hints import get_pending_events

    pending_events = 0
    if agent is not None:
        pending_events = await get_pending_events(db, agent)

    return {
        "leaderboard": rankings[:50],  # Top 50
        "total_agents": len(rankings),
        "your_rank": my_rank,
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 300,
            "message": (
                f"Leaderboard shows {len(rankings)} active agents. " + (f"Your rank: #{my_rank}." if my_rank else "")
            ),
        },
    }
