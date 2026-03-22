"""
Tests for marketplace bug fixes:
1. Buy order auto-cancel when buyer storage is full during matching
2. Minimum cancel fee of $0.01 enforced on tiny orders
3. Correct refund calculation (locked value minus cancel fee)
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.inventory import InventoryItem
from backend.models.marketplace import MarketOrder
from tests.helpers import TestAgent


@pytest.mark.asyncio
async def test_buy_order_auto_cancelled_when_storage_full(
    client, app, clock, db, redis_client, run_tick
):
    """
    When a buy order matches but the buyer's inventory is full,
    the buy order should be auto-cancelled with a refund (minus cancel fee),
    not left open with funds locked forever.
    """
    buyer = await TestAgent.signup(client, "sfull_buyer")
    seller = await TestAgent.signup(client, "sfull_seller")

    # Give buyer balance and seller balance + goods via a single session
    async with app.state.session_factory() as session:
        b_result = await session.execute(
            select(Agent).where(Agent.name == "sfull_buyer").with_for_update()
        )
        buyer_ag = b_result.scalar_one()
        buyer_ag.balance = Decimal("500.00")

        s_result = await session.execute(
            select(Agent).where(Agent.name == "sfull_seller").with_for_update()
        )
        seller_ag = s_result.scalar_one()
        seller_ag.balance = Decimal("100.00")

        # Add wood to seller
        session.add(InventoryItem(
            owner_type="agent", owner_id=seller_ag.id,
            good_slug="wood", quantity=5,
        ))

        # Fill buyer storage to capacity (100)
        session.add(InventoryItem(
            owner_type="agent", owner_id=buyer_ag.id,
            good_slug="berries", quantity=100,
        ))
        await session.commit()

    # Seller places sell order
    sell_result = await seller.call("marketplace_order", {
        "action": "sell", "product": "wood", "quantity": 1, "price": 5.00,
    })
    assert sell_result["order"]["status"] in ("open", "partially_filled", "filled")

    # Buyer places buy order — should match but storage full -> auto-cancel
    buy_result = await buyer.call("marketplace_order", {
        "action": "buy", "product": "wood", "quantity": 1, "price": 5.00,
    })
    order_id = buy_result["order"]["id"]

    # Verify order was auto-cancelled
    async with app.state.session_factory() as session:
        result = await session.execute(
            select(MarketOrder).where(MarketOrder.id == order_id)
        )
        order = result.scalar_one()
        assert order.status == "cancelled", (
            f"Buy order should be auto-cancelled when storage full, got {order.status}"
        )

    # Buyer should get refund: locked 5.00, cancel fee 2% = 0.10, refund = 4.90
    # So balance = 500 - 5.00 + 4.90 = 499.90
    buyer_status = await buyer.status()
    assert abs(buyer_status["balance"] - 499.90) < 0.02, (
        f"Buyer balance should be ~499.90, got {buyer_status['balance']}"
    )
    print("Buy order auto-cancelled on storage full with correct refund")


@pytest.mark.asyncio
async def test_minimum_cancel_fee_enforced(client, app, clock, db, redis_client):
    """
    The 2% cancel fee should have a minimum of $0.01 to prevent
    free spoofing on tiny orders.
    """
    agent = await TestAgent.signup(client, "minfee_agent")

    async with app.state.session_factory() as session:
        result = await session.execute(
            select(Agent).where(Agent.name == "minfee_agent").with_for_update()
        )
        ag = result.scalar_one()
        ag.balance = Decimal("10.00")
        await session.commit()

    # Place a tiny buy order: 1 unit at $0.10 (2% = 0.002, rounds to 0.00)
    result = await agent.call("marketplace_order", {
        "action": "buy", "product": "berries", "quantity": 1, "price": 0.10,
    })
    order_id = result["order"]["id"]

    # Cancel — fee should be $0.01 minimum
    cancel_result = await agent.call("marketplace_order", {
        "action": "cancel", "order_id": order_id,
    })

    assert cancel_result["cancelled"] is True
    cancel_fee = cancel_result["refund"]["cancel_fee"]
    assert cancel_fee >= 0.01, f"Cancel fee should be >= $0.01, got {cancel_fee}"

    refund_amount = cancel_result["refund"]["amount"]
    assert abs(refund_amount - 0.09) < 0.005, f"Refund should be ~$0.09, got {refund_amount}"
    print(f"Minimum cancel fee enforced: fee={cancel_fee}, refund={refund_amount}")


@pytest.mark.asyncio
async def test_cancel_refund_correct(client, app, clock, db, redis_client):
    """
    Test that cancel refund equals locked_value minus cancel_fee exactly.
    """
    agent = await TestAgent.signup(client, "crefund_agent")

    async with app.state.session_factory() as session:
        result = await session.execute(
            select(Agent).where(Agent.name == "crefund_agent").with_for_update()
        )
        ag = result.scalar_one()
        ag.balance = Decimal("1000.00")
        await session.commit()

    pre_status = await agent.status()
    pre_balance = pre_status["balance"]

    # Place buy order: 5 x $20 = $100 locked (use stone to avoid matching stale orders)
    result = await agent.call("marketplace_order", {
        "action": "buy", "product": "stone", "quantity": 5, "price": 20.00,
    })
    order_id = result["order"]["id"]

    status = await agent.status()
    assert abs(status["balance"] - (pre_balance - 100.0)) < 0.01

    # Cancel — fee = 2% of 100 = $2.00, refund = $98.00
    cancel_result = await agent.call("marketplace_order", {
        "action": "cancel", "order_id": order_id,
    })

    assert cancel_result["cancelled"] is True
    cancel_fee = cancel_result["refund"]["cancel_fee"]
    refund_amount = cancel_result["refund"]["amount"]

    assert abs(cancel_fee - 2.00) < 0.01, f"Fee should be $2.00, got {cancel_fee}"
    assert abs(refund_amount - 98.00) < 0.01, f"Refund should be $98.00, got {refund_amount}"

    final_status = await agent.status()
    assert abs(final_status["balance"] - (pre_balance - 2.00)) < 0.01, (
        f"Final balance should be {pre_balance - 2.00}, got {final_status['balance']}"
    )
    print(f"Cancel refund correct: fee={cancel_fee}, refund={refund_amount}")
