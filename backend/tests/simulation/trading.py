"""Marketplace & Trading: orders, matching, direct trades, concurrency safety.

Covers:
- Sell/buy orders with immediate fill verification
- Order matching via tick, fill price verification
- Browse marketplace (product-specific + all)
- Cancel order with 2% fee verification
- Market buy (max-price instant fill)
- Self-trade prevention (verified via DB)
- Wash trading prevention (verified via DB)
- Storage-full handling (gather blocked, buy order auto-cancelled)
- Concurrency: balance double-spend prevention
- Concurrency: inventory double-spend prevention
- Concurrency: TOCTOU on trade response
- Direct trades: propose, accept, reject, cancel with escrow
- Messaging between agents
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import select

from backend.models.marketplace import MarketOrder, MarketTrade
from tests.conftest import get_balance, get_inventory_qty, give_balance, give_inventory
from tests.helpers import TestAgent
from tests.simulation.helpers import print_section, print_stage


async def run_trading(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Exercise marketplace, direct trades, messaging, and concurrency safety."""
    print_stage("MARKETPLACE & TRADING")

    await give_balance(app, "eco_trader", 1000)
    await give_balance(app, "eco_gatherer1", 500)
    await give_balance(app, "eco_gatherer2", 500)
    await give_inventory(app, "eco_gatherer1", "berries", 30)
    await give_inventory(app, "eco_gatherer2", "berries", 20)

    # ------------------------------------------------------------------
    # Place sell orders
    # ------------------------------------------------------------------
    print_section("Sell orders")

    sell1 = await agents["eco_gatherer1"].call(
        "marketplace_order",
        {"action": "sell", "product": "berries", "quantity": 15, "price": 4.0},
    )
    assert sell1["order"]["side"] == "sell"

    await agents["eco_gatherer2"].call(
        "marketplace_order",
        {"action": "sell", "product": "berries", "quantity": 10, "price": 5.0},
    )
    print("  gatherer1: sell 15 berries@4, gatherer2: sell 10 berries@5")

    # ------------------------------------------------------------------
    # Matching buy order + fill verification
    # ------------------------------------------------------------------
    print_section("Matching buy order")

    trader_bal_before = await get_balance(app, "eco_trader")
    buy1 = await agents["eco_trader"].call(
        "marketplace_order",
        {"action": "buy", "product": "berries", "quantity": 20, "price": 6.0},
    )
    fills = buy1["immediate_fills"]
    print(f"  trader: buy 20 berries@6, immediate_fills={fills}")

    await run_tick(minutes=1)

    trader_berries = await get_inventory_qty(app, "eco_trader", "berries")
    assert trader_berries == 20, f"Trader should have 20 berries, got {trader_berries}"

    # Price check: 15@4 + 5@5 = 60 + 25 = 85
    trader_bal_after = await get_balance(app, "eco_trader")
    actual_spent = float(trader_bal_before - trader_bal_after)
    assert abs(actual_spent - 85) < 20, f"Trader should spend ~85, spent {actual_spent:.2f}"
    print(f"  Trader received 20 berries, cost ~{actual_spent:.2f}")

    # ------------------------------------------------------------------
    # Browse marketplace
    # ------------------------------------------------------------------
    print_section("Browse marketplace")

    browse = await agents["eco_trader"].call("marketplace_browse", {"product": "berries"})
    assert "bids" in browse
    assert "asks" in browse
    assert "recent_trades" in browse
    assert len(browse["recent_trades"]) > 0, "Should have recent trades after matching"
    print(f"  {len(browse['recent_trades'])} recent trades for berries")

    browse_all = await agents["eco_trader"].call("marketplace_browse", {})
    assert "summary" in browse_all or "goods" in browse_all or "items" in browse_all
    print("  Browse all products: OK")

    # ------------------------------------------------------------------
    # Cancel order with 2% fee
    # ------------------------------------------------------------------
    print_section("Cancel order with fee")

    # Use a fresh agent + product with no existing orders to avoid fills
    cancel_agent = await TestAgent.signup(client, "cancel_fee_agent")
    await give_balance(app, "cancel_fee_agent", 200)
    bal_before_order = await get_balance(app, "cancel_fee_agent")

    # Place a buy order: 5 stone @ $10 = $50 locked (stone has no sell orders)
    order = await cancel_agent.call(
        "marketplace_order",
        {"action": "buy", "product": "stone", "quantity": 5, "price": 10},
    )
    cancel_order_id = order["order"]["id"]
    bal_after_order = await get_balance(app, "cancel_fee_agent")
    locked = bal_before_order - bal_after_order
    assert locked == Decimal("50"), f"Expected $50 locked, got {locked}"

    # Cancel
    cancel_result = await cancel_agent.call(
        "marketplace_order",
        {"action": "cancel", "order_id": cancel_order_id},
    )
    assert cancel_result["cancelled"] is True
    bal_after_cancel = await get_balance(app, "cancel_fee_agent")

    # 2% fee on $50 = $1, refund = $49
    refund = bal_after_cancel - bal_after_order
    assert refund == Decimal("49"), f"Expected refund $49 (2% fee), got {refund}"
    fee_paid = bal_before_order - bal_after_cancel
    assert fee_paid == Decimal("1"), f"Expected fee $1, got {fee_paid}"
    print("  Cancel fee=$1, refund=$49 on $50 order")

    # ------------------------------------------------------------------
    # Market buy (max-price instant fill)
    # ------------------------------------------------------------------
    print_section("Market buy")

    await give_inventory(app, "eco_gatherer2", "wood", 5)
    await agents["eco_gatherer2"].call(
        "marketplace_order",
        {"action": "sell", "product": "wood", "quantity": 3, "price": 2.0},
    )
    await give_balance(app, "eco_trader", 1000)
    await agents["eco_trader"].call(
        "marketplace_order",
        {"action": "buy", "product": "wood", "quantity": 3},
    )
    await run_tick(minutes=1)
    trader_wood = await get_inventory_qty(app, "eco_trader", "wood")
    assert trader_wood >= 3
    print(f"  Market buy filled, trader has {trader_wood} wood")

    # ------------------------------------------------------------------
    # Self-trade prevention (verified via DB)
    # ------------------------------------------------------------------
    print_section("Self-trade prevention")

    await give_inventory(app, "eco_trader", "herbs", 10)
    await give_balance(app, "eco_trader", 500)

    self_sell = await agents["eco_trader"].call(
        "marketplace_order",
        {"action": "sell", "product": "herbs", "quantity": 5, "price": 3.0},
    )
    self_buy = await agents["eco_trader"].call(
        "marketplace_order",
        {"action": "buy", "product": "herbs", "quantity": 5, "price": 3.0},
    )
    sell_oid = self_sell["order"]["id"]
    buy_oid = self_buy["order"]["id"]

    await run_tick(minutes=1)

    # DB verification: no self-trade should exist
    async with app.state.session_factory() as session:
        self_trades = (
            (
                await session.execute(
                    select(MarketTrade).where(
                        MarketTrade.buy_order_id == buy_oid,
                        MarketTrade.sell_order_id == sell_oid,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(self_trades) == 0, f"Self-trade detected: {len(self_trades)} trades between own orders"
        # Note: orders may fill against NPC orders, which is fine — the key
        # invariant is that no MarketTrade exists between the agent's own orders.
    print("  Self-trade prevention verified via DB (no self-match)")

    # Clean up
    for oid in [sell_oid, buy_oid]:
        try:
            await agents["eco_trader"].call("marketplace_order", {"action": "cancel", "order_id": oid})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Wash trading prevention (verified via DB)
    # ------------------------------------------------------------------
    print_section("Wash trading prevention")

    # Use iron_ore — a product with no existing orders on the book
    wash_agent = await TestAgent.signup(client, "wash_trader")
    await give_balance(app, "wash_trader", 500)
    await give_inventory(app, "wash_trader", "iron_ore", 20)

    wash_sell = await wash_agent.call(
        "marketplace_order",
        {"action": "sell", "product": "iron_ore", "quantity": 5, "price": 50},
    )
    wash_buy = await wash_agent.call(
        "marketplace_order",
        {"action": "buy", "product": "iron_ore", "quantity": 5, "price": 50},
    )
    # Neither order should self-fill at placement time
    assert wash_sell.get("immediate_fills", 0) == 0, "Sell should not self-fill"
    assert wash_buy.get("immediate_fills", 0) == 0, "Buy should not self-fill"

    await run_tick(minutes=2)

    # Verify via DB: no trade between the agent's own orders
    async with app.state.session_factory() as session:
        wash_trades = (
            (
                await session.execute(
                    select(MarketTrade).where(
                        MarketTrade.buy_order_id == wash_buy["order"]["id"],
                        MarketTrade.sell_order_id == wash_sell["order"]["id"],
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(wash_trades) == 0, f"Wash trade detected: {len(wash_trades)} self-trades"
    print("  Wash trading prevention verified via DB")

    for oid in [wash_sell["order"]["id"], wash_buy["order"]["id"]]:
        try:
            await wash_agent.call("marketplace_order", {"action": "cancel", "order_id": str(oid)})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Storage-full handling
    # ------------------------------------------------------------------
    print_section("Storage-full handling")

    storage_agent = await TestAgent.signup(client, "storage_full_agent")
    await give_balance(app, "storage_full_agent", 500)
    await give_inventory(app, "storage_full_agent", "berries", 100)
    clock.advance(120)

    # Gather blocked when storage full
    _, err = await storage_agent.try_call("gather", {"resource": "wood"})
    assert err == "STORAGE_FULL", f"Expected STORAGE_FULL, got {err}"
    print("  Gather blocked at full storage")

    # Buy order auto-cancelled when storage full
    seller = await TestAgent.signup(client, "storage_seller")
    await give_balance(app, "storage_seller", 100)
    await give_inventory(app, "storage_seller", "wood", 10)

    buy_result = await storage_agent.call(
        "marketplace_order",
        {"action": "buy", "product": "wood", "quantity": 2, "price": 10},
    )
    storage_buy_id = buy_result["order"]["id"]

    await seller.call(
        "marketplace_order",
        {"action": "sell", "product": "wood", "quantity": 2, "price": 10},
    )

    async with app.state.session_factory() as session:
        buy_order = (
            await session.execute(select(MarketOrder).where(MarketOrder.id == storage_buy_id))
        ).scalar_one_or_none()
        if buy_order:
            assert buy_order.status == "cancelled", (
                f"Expected buy order cancelled (storage full), got {buy_order.status}"
            )
    print("  Buy order auto-cancelled due to full storage")

    # ------------------------------------------------------------------
    # Concurrency: balance double-spend prevention
    # ------------------------------------------------------------------
    print_section("Concurrency: balance double-spend")

    ds_agent = await TestAgent.signup(client, "dbl_spend_bal")
    await TestAgent.signup(client, "dbl_spend_tgt")
    await give_balance(app, "dbl_spend_bal", 100)

    async def propose_money_trade(target_name):
        return await ds_agent.try_call(
            "trade",
            {
                "action": "propose",
                "target_agent": target_name,
                "offer_items": [],
                "request_items": [],
                "offer_money": 80,
                "request_money": 0,
            },
        )

    results = await asyncio.gather(
        propose_money_trade("dbl_spend_tgt"),
        propose_money_trade("wash_trader"),
        return_exceptions=True,
    )
    successes = sum(1 for r in results if not isinstance(r, Exception) and r[1] is None)
    assert successes <= 1, f"Double-spend: {successes} trades succeeded with only 100 balance"

    bal = await get_balance(app, "dbl_spend_bal")
    assert bal >= 0, f"Balance went negative: {bal}"
    print(f"  {successes} of 2 concurrent 80-money trades succeeded (balance={bal})")

    # ------------------------------------------------------------------
    # Concurrency: inventory double-spend prevention
    # ------------------------------------------------------------------
    print_section("Concurrency: inventory double-spend")

    inv_agent = await TestAgent.signup(client, "dbl_spend_inv")
    await TestAgent.signup(client, "inv_tgt1")
    await TestAgent.signup(client, "inv_tgt2")
    await give_inventory(app, "dbl_spend_inv", "berries", 5)
    await give_balance(app, "dbl_spend_inv", 100)

    async def propose_item_trade(target_name):
        return await inv_agent.try_call(
            "trade",
            {
                "action": "propose",
                "target_agent": target_name,
                "offer_items": [{"good_slug": "berries", "quantity": 4}],
                "request_items": [],
                "offer_money": 0,
                "request_money": 0,
            },
        )

    results = await asyncio.gather(
        propose_item_trade("inv_tgt1"),
        propose_item_trade("inv_tgt2"),
        return_exceptions=True,
    )
    successes = sum(1 for r in results if not isinstance(r, Exception) and r[1] is None)
    assert successes <= 1, f"Inventory double-spend: {successes} trades with only 5 berries for 2x4"

    inv_qty = await get_inventory_qty(app, "dbl_spend_inv", "berries")
    assert inv_qty >= 0, f"Inventory went negative: {inv_qty}"
    print(f"  {successes} of 2 concurrent 4-berry trades succeeded (remaining={inv_qty})")

    # ------------------------------------------------------------------
    # Concurrency: TOCTOU on trade response
    # ------------------------------------------------------------------
    print_section("Concurrency: TOCTOU on trade accept")

    prop1 = await TestAgent.signup(client, "toctou_prop1")
    prop2 = await TestAgent.signup(client, "toctou_prop2")
    toctou_target = await TestAgent.signup(client, "toctou_target")
    await give_balance(app, "toctou_prop1", 100)
    await give_balance(app, "toctou_prop2", 100)
    await give_inventory(app, "toctou_target", "berries", 5)
    await give_balance(app, "toctou_target", 100)

    t1 = await prop1.call(
        "trade",
        {
            "action": "propose",
            "target_agent": "toctou_target",
            "offer_items": [],
            "request_items": [{"good_slug": "berries", "quantity": 4}],
            "offer_money": 10,
            "request_money": 0,
        },
    )
    t2 = await prop2.call(
        "trade",
        {
            "action": "propose",
            "target_agent": "toctou_target",
            "offer_items": [],
            "request_items": [{"good_slug": "berries", "quantity": 4}],
            "offer_money": 10,
            "request_money": 0,
        },
    )

    accept_results = await asyncio.gather(
        toctou_target.try_call("trade", {"action": "respond", "trade_id": t1["trade"]["id"], "accept": True}),
        toctou_target.try_call("trade", {"action": "respond", "trade_id": t2["trade"]["id"], "accept": True}),
        return_exceptions=True,
    )
    accept_successes = sum(1 for r in accept_results if not isinstance(r, Exception) and r[1] is None)
    assert accept_successes <= 1, f"TOCTOU: {accept_successes} accepts with only 5 berries for 2x4"

    target_berries = await get_inventory_qty(app, "toctou_target", "berries")
    assert target_berries >= 0, f"Target inventory went negative: {target_berries}"
    print(f"  {accept_successes} of 2 concurrent accepts succeeded (target berries={target_berries})")

    # ------------------------------------------------------------------
    # Direct trade: propose + accept
    # ------------------------------------------------------------------
    print_section("Direct trades")

    agent_a = agents["eco_gatherer1"]
    agent_b = agents["eco_gatherer2"]
    await give_balance(app, "eco_gatherer1", 200)
    await give_balance(app, "eco_gatherer2", 200)
    await give_inventory(app, "eco_gatherer1", "berries", 20)
    await give_inventory(app, "eco_gatherer2", "wood", 15)

    # Propose trade: 8 berries + $5 for 4 wood
    propose = await agent_a.call(
        "trade",
        {
            "action": "propose",
            "target_agent": "eco_gatherer2",
            "offer_items": [{"good_slug": "berries", "quantity": 8}],
            "request_items": [{"good_slug": "wood", "quantity": 4}],
            "offer_money": 5.0,
            "request_money": 0.0,
        },
    )
    trade_id = propose["trade"]["id"]
    assert propose["trade"]["status"] == "pending"

    # Verify escrow
    a_berries = await get_inventory_qty(app, "eco_gatherer1", "berries")
    assert a_berries == 12, f"A should have 12 berries (8 escrowed), has {a_berries}"
    a_bal = await get_balance(app, "eco_gatherer1")
    assert float(a_bal) < 200, "A's balance should be reduced by escrow"

    # Accept
    accept = await agent_b.call("trade", {"action": "respond", "trade_id": trade_id, "accept": True})
    assert accept["status"] == "accepted"

    a_wood = await get_inventory_qty(app, "eco_gatherer1", "wood")
    b_berries = await get_inventory_qty(app, "eco_gatherer2", "berries")
    assert a_wood >= 4, f"A should have received 4 wood, has {a_wood}"
    assert b_berries >= 8, f"B should have received 8 berries, has {b_berries}"
    print("  Propose+accept: escrow locked, exchange completed")

    # Propose + reject: escrow returned
    await give_inventory(app, "eco_gatherer1", "berries", 10)
    berries_before = await get_inventory_qty(app, "eco_gatherer1", "berries")
    propose2 = await agent_a.call(
        "trade",
        {
            "action": "propose",
            "target_agent": "eco_gatherer2",
            "offer_items": [{"good_slug": "berries", "quantity": 3}],
            "request_items": [{"good_slug": "wood", "quantity": 2}],
        },
    )
    reject = await agent_b.call("trade", {"action": "respond", "trade_id": propose2["trade"]["id"], "accept": False})
    assert reject["status"] == "rejected"
    berries_after = await get_inventory_qty(app, "eco_gatherer1", "berries")
    assert berries_after == berries_before, f"Escrow not returned: {berries_before} → {berries_after}"
    print("  Propose+reject: escrow returned")

    # Propose + cancel: escrow returned
    await give_inventory(app, "eco_gatherer1", "stone", 5)
    stone_before = await get_inventory_qty(app, "eco_gatherer1", "stone")
    propose3 = await agent_a.call(
        "trade",
        {
            "action": "propose",
            "target_agent": "eco_gatherer2",
            "offer_items": [{"good_slug": "stone", "quantity": 3}],
            "request_items": [{"good_slug": "wood", "quantity": 1}],
        },
    )
    cancel = await agent_a.call("trade", {"action": "cancel", "trade_id": propose3["trade"]["id"]})
    assert cancel["status"] == "cancelled"
    stone_after = await get_inventory_qty(app, "eco_gatherer1", "stone")
    assert stone_after == stone_before, f"Escrow not returned: {stone_before} → {stone_after}"
    print("  Propose+cancel: escrow returned")

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------
    print_section("Messaging")

    send = await agents["eco_trader"].call(
        "messages",
        {"action": "send", "to_agent": "eco_baker", "text": "I have 20 berries to sell. Interested?"},
    )
    assert "message_id" in send or "sent" in str(send).lower()

    read = await agents["eco_baker"].call("messages", {"action": "read"})
    assert "messages" in read
    assert len(read["messages"]) > 0
    assert any("berries" in m.get("text", "") for m in read["messages"])
    print("  Message sent and received")

    # ------------------------------------------------------------------
    # Run 5 days of ticks (covers Phase 4+5 time period)
    # ------------------------------------------------------------------
    # Top up main simulation agents so they survive the long tick
    from tests.simulation.helpers import AGENT_NAMES

    for name in AGENT_NAMES:
        if name != "eco_homeless":
            await give_balance(app, name, 2000)
    await run_tick(hours=120)

    print("\n  Marketplace & Trading COMPLETE")
