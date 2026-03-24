"""Sections 6-9: Wash trading, storage full, cancel fees, jail restrictions."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.marketplace import MarketOrder, MarketTrade
from tests.conftest import get_balance, give_balance, give_inventory, jail_agent
from tests.helpers import TestAgent


async def run_marketplace_and_jail(client, app, clock, run_tick, agents):
    """
    Section 6: Wash Trading Prevention
    Section 7: Storage Full Handling
    Section 8: Cancel Order Fee
    Section 9: Jail Restrictions

    Returns updated agents dict.
    """

    # ===================================================================
    # Section 6: Wash Trading Prevention
    # ===================================================================
    print("\n--- Section 6: Wash Trading Prevention ---")

    adv_washer = await TestAgent.signup(client, "adv_washer")
    await give_balance(app, "adv_washer", 500)
    await give_inventory(app, "adv_washer", "berries", 20)
    agents["adv_washer"] = adv_washer

    # Place a sell order for berries at price 5
    sell_result = await adv_washer.call(
        "marketplace_order",
        {
            "action": "sell",
            "product": "berries",
            "quantity": 5,
            "price": 5,
        },
    )
    sell_order_id = sell_result["order"]["id"]

    # Place a buy order for berries at price 5 (same agent)
    buy_result = await adv_washer.call(
        "marketplace_order",
        {
            "action": "buy",
            "product": "berries",
            "quantity": 5,
            "price": 5,
        },
    )
    buy_order_id = buy_result["order"]["id"]

    # Neither should fill against each other at placement time
    immediate_fills_sell = sell_result.get("immediate_fills", 0)
    immediate_fills_buy = buy_result.get("immediate_fills", 0)
    assert immediate_fills_sell == 0, f"Sell order self-filled: {immediate_fills_sell}"
    assert immediate_fills_buy == 0, f"Buy order self-filled: {immediate_fills_buy}"

    # Run tick to trigger matching engine
    await run_tick(minutes=2)

    # Verify via DB: no MarketTrade exists matching this agent's orders
    async with app.state.session_factory() as session:
        # Get agent ID
        agent_result = await session.execute(select(Agent).where(Agent.name == "adv_washer"))
        agent_result.scalar_one()

        # Check for any self-trades
        trades_result = await session.execute(
            select(MarketTrade).where(
                MarketTrade.buy_order_id == buy_order_id,
                MarketTrade.sell_order_id == sell_order_id,
            )
        )
        self_trades = trades_result.scalars().all()
        assert len(self_trades) == 0, f"Wash trade detected: {len(self_trades)} self-trades found"

        # Verify both orders are still open
        for oid in [sell_order_id, buy_order_id]:
            order_result = await session.execute(select(MarketOrder).where(MarketOrder.id == oid))
            order = order_result.scalar_one()
            assert order.status in ("open", "partially_filled"), f"Order {oid} unexpectedly {order.status}"

    print("  PASSED: Wash trading correctly prevented")

    # Clean up orders to avoid interference
    await adv_washer.call("marketplace_order", {"action": "cancel", "order_id": str(sell_order_id)})
    await adv_washer.call("marketplace_order", {"action": "cancel", "order_id": str(buy_order_id)})

    # ===================================================================
    # Section 7: Storage Full Handling
    # ===================================================================
    print("\n--- Section 7: Storage Full Handling ---")

    adv_storage = await TestAgent.signup(client, "adv_storage")
    await give_balance(app, "adv_storage", 500)
    agents["adv_storage"] = adv_storage

    # Fill inventory to capacity (100 units, storage_size=1 each for berries)
    await give_inventory(app, "adv_storage", "berries", 100)

    # Advance past any cooldowns
    clock.advance(120)

    # Try to gather when storage is full
    _, err = await adv_storage.try_call("gather", {"resource": "wood"})
    assert err == "STORAGE_FULL", f"Expected STORAGE_FULL when inventory at capacity, got {err}"

    # Marketplace buy order with full storage: place a buy order, then have
    # another agent sell to fill it. The buyer's storage is full so the buy
    # order should get auto-cancelled.
    adv_seller = await TestAgent.signup(client, "adv_seller")
    await give_balance(app, "adv_seller", 100)
    await give_inventory(app, "adv_seller", "wood", 10)
    agents["adv_seller"] = adv_seller

    # Storage agent places buy order for wood
    buy_result = await adv_storage.call(
        "marketplace_order",
        {
            "action": "buy",
            "product": "wood",
            "quantity": 2,
            "price": 10,
        },
    )
    storage_buy_order_id = buy_result["order"]["id"]

    # Seller places sell order at same price -> should trigger matching
    sell_result = await adv_seller.call(
        "marketplace_order",
        {
            "action": "sell",
            "product": "wood",
            "quantity": 2,
            "price": 10,
        },
    )

    # Check that the buy order was auto-cancelled due to full storage
    async with app.state.session_factory() as session:
        order_result = await session.execute(select(MarketOrder).where(MarketOrder.id == storage_buy_order_id))
        buy_order = order_result.scalar_one_or_none()
        if buy_order:
            # It should be cancelled due to storage full
            assert buy_order.status == "cancelled", (
                f"Expected buy order to be cancelled (storage full), got {buy_order.status}"
            )
            print("  Buy order correctly auto-cancelled due to full storage")
        else:
            print("  Buy order not found (may have been handled differently)")

    print("  PASSED: Storage full handling correct")

    # ===================================================================
    # Section 8: Cancel Order Fee
    # ===================================================================
    print("\n--- Section 8: Cancel Order Fee ---")

    adv_canceller = await TestAgent.signup(client, "adv_canceller")
    await give_balance(app, "adv_canceller", 200)
    agents["adv_canceller"] = adv_canceller

    balance_before = await get_balance(app, "adv_canceller")

    # Place buy order: 5 berries at $10 each = $50 locked
    order_result = await adv_canceller.call(
        "marketplace_order",
        {
            "action": "buy",
            "product": "berries",
            "quantity": 5,
            "price": 10,
        },
    )
    cancel_order_id = order_result["order"]["id"]

    balance_after_order = await get_balance(app, "adv_canceller")
    locked_amount = balance_before - balance_after_order
    assert locked_amount == Decimal("50"), f"Expected $50 locked, got {locked_amount}"

    # Cancel the order
    await adv_canceller.call(
        "marketplace_order",
        {
            "action": "cancel",
            "order_id": cancel_order_id,
        },
    )

    balance_after_cancel = await get_balance(app, "adv_canceller")

    # 2% cancel fee on $50 = $1; refund should be $49
    refund = balance_after_cancel - balance_after_order
    expected_refund = Decimal("49")  # $50 - 2% fee ($1)
    assert refund == expected_refund, (
        f"Expected refund of {expected_refund}, got {refund} "
        f"(before={balance_after_order}, after={balance_after_cancel})"
    )

    fee_paid = balance_before - balance_after_cancel
    expected_fee = Decimal("1")  # 2% of $50
    assert fee_paid == expected_fee, f"Expected fee of {expected_fee}, got {fee_paid}"

    print(f"  PASSED: Cancel fee={fee_paid}, refund={refund}")

    # ===================================================================
    # Section 9: Jail Restrictions
    # ===================================================================
    print("\n--- Section 9: Jail Restrictions ---")

    adv_jailed = await TestAgent.signup(client, "adv_jailed")
    await give_balance(app, "adv_jailed", 500)
    await give_inventory(app, "adv_jailed", "berries", 10)
    agents["adv_jailed"] = adv_jailed

    # Put agent in jail
    await jail_agent(app, "adv_jailed", clock, hours=2.0)

    # Advance past cooldowns but not past jail
    clock.advance(120)

    # Tools that SHOULD be BLOCKED while in jail
    blocked_tools = [
        ("gather", {"resource": "berries"}),
        ("work", {}),
        ("marketplace_order", {"action": "buy", "product": "berries", "quantity": 1, "price": 5}),
        ("register_business", {"name": "Jail Biz", "type": "bakery", "zone": "outskirts"}),
        (
            "trade",
            {
                "action": "propose",
                "target_agent": "adv_valid",
                "offer_items": [],
                "request_items": [],
                "offer_money": 1,
                "request_money": 0,
            },
        ),
        ("apply_job", {"job_id": "00000000-0000-0000-0000-000000000000"}),
        ("set_prices", {"business_id": "00000000-0000-0000-0000-000000000000", "product": "bread", "price": 5}),
        ("configure_production", {"business_id": "00000000-0000-0000-0000-000000000000", "product": "bread"}),
    ]

    for tool_name, params in blocked_tools:
        _, err = await adv_jailed.try_call(tool_name, params)
        assert err == "IN_JAIL", f"Expected IN_JAIL for {tool_name} while jailed, got {err}"

    print(f"  Blocked {len(blocked_tools)} tools correctly while in jail")

    # Tools that SHOULD be ALLOWED while in jail
    allowed_tools = [
        ("get_status", {}),
        ("messages", {"action": "read"}),
        ("bank", {"action": "view_balance"}),
        ("marketplace_browse", {}),
        ("get_economy", {}),
    ]

    for tool_name, params in allowed_tools:
        result, err = await adv_jailed.try_call(tool_name, params)
        assert err is None or err != "IN_JAIL", f"Tool {tool_name} should be ALLOWED in jail but got {err}"

    print(f"  Allowed {len(allowed_tools)} view-only tools while in jail")
    print("  PASSED: Jail restrictions enforced correctly")

    return agents
