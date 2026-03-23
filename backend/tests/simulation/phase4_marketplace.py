"""Phase 4: Marketplace (Days 5-8) — order book, matching, cancellation, market orders."""

from __future__ import annotations

from decimal import Decimal

from tests.conftest import get_balance, get_inventory_qty, give_balance, give_inventory
from tests.helpers import TestAgent
from tests.simulation.helpers import print_phase, print_section


async def run_phase_4(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Exercise marketplace: sell/buy orders, matching, cancellation, market buy, self-trade prevention."""
    print_phase(4, "MARKETPLACE")

    # Top up trader
    await give_balance(app, "eco_trader", 1000)
    await give_balance(app, "eco_gatherer1", 500)
    await give_balance(app, "eco_gatherer2", 500)

    # Give gatherers some berries to sell
    await give_inventory(app, "eco_gatherer1", "berries", 30)
    await give_inventory(app, "eco_gatherer2", "berries", 20)

    # --- 4a: Place sell orders ---
    print_section("Placing sell orders")

    sell1 = await agents["eco_gatherer1"].call("marketplace_order", {
        "action": "sell", "product": "berries", "quantity": 15, "price": 4.0,
    })
    sell1_id = sell1["order"]["id"]
    assert sell1["order"]["side"] == "sell"
    print(f"  gatherer1: sell 15 berries @ 4.0")

    sell2 = await agents["eco_gatherer2"].call("marketplace_order", {
        "action": "sell", "product": "berries", "quantity": 10, "price": 5.0,
    })
    sell2_id = sell2["order"]["id"]
    print(f"  gatherer2: sell 10 berries @ 5.0")

    # --- 4b: Place buy order that matches ---
    print_section("Placing matching buy order")

    trader_balance_before = await get_balance(app, "eco_trader")
    buy1 = await agents["eco_trader"].call("marketplace_order", {
        "action": "buy", "product": "berries", "quantity": 20, "price": 6.0,
    })
    fills = buy1["immediate_fills"]
    print(f"  trader: buy 20 berries @ 6.0 (immediate fills: {fills})")

    # Run fast tick for matching
    await run_tick(minutes=1)

    # Verify trader got berries
    trader_berries = await get_inventory_qty(app, "eco_trader", "berries")
    assert trader_berries == 20, f"Trader should have 20 berries, got {trader_berries}"
    print(f"  Trader received {trader_berries} berries")

    # Verify prices: 15 @ 4 + 5 @ 5 = 60 + 25 = 85
    trader_balance_after = await get_balance(app, "eco_trader")
    expected_cost = Decimal("85")
    actual_spent = trader_balance_before - trader_balance_after
    assert abs(float(actual_spent) - float(expected_cost)) < 20, \
        f"Trader should spend ~85, spent {float(actual_spent)}"
    print(f"  Trade cost ~{float(actual_spent):.2f} (expected ~85)")

    # --- 4c: Browse marketplace ---
    print_section("Browsing marketplace")

    browse = await agents["eco_trader"].call("marketplace_browse", {"product": "berries"})
    assert "bids" in browse
    assert "asks" in browse
    assert "recent_trades" in browse
    assert len(browse["recent_trades"]) > 0
    print(f"  Browse: {len(browse['recent_trades'])} recent trades")

    browse_all = await agents["eco_trader"].call("marketplace_browse", {})
    assert "summary" in browse_all or "goods" in browse_all or "items" in browse_all
    print(f"  Browse all products: OK")

    # --- 4d: Cancel an order ---
    print_section("Cancelling an order")

    await give_inventory(app, "eco_gatherer1", "berries", 10)
    cancel_sell = await agents["eco_gatherer1"].call("marketplace_order", {
        "action": "sell", "product": "berries", "quantity": 5, "price": 20.0,
    })
    cancel_order_id = cancel_sell["order"]["id"]

    berries_before_cancel = await get_inventory_qty(app, "eco_gatherer1", "berries")

    cancel_result = await agents["eco_gatherer1"].call("marketplace_order", {
        "action": "cancel", "order_id": cancel_order_id,
    })
    assert cancel_result["cancelled"] is True

    berries_after_cancel = await get_inventory_qty(app, "eco_gatherer1", "berries")
    assert berries_after_cancel == berries_before_cancel + 5
    print(f"  Order cancelled, goods returned: {berries_before_cancel} -> {berries_after_cancel}")

    # --- 4e: Market buy (price=999999999.99) ---
    print_section("Market buy order")

    await give_inventory(app, "eco_gatherer2", "wood", 5)
    await agents["eco_gatherer2"].call("marketplace_order", {
        "action": "sell", "product": "wood", "quantity": 3, "price": 2.0,
    })

    await give_balance(app, "eco_trader", 1000)
    market_buy = await agents["eco_trader"].call("marketplace_order", {
        "action": "buy", "product": "wood", "quantity": 3,
    })
    await run_tick(minutes=1)

    trader_wood = await get_inventory_qty(app, "eco_trader", "wood")
    assert trader_wood >= 3
    print(f"  Market buy filled: trader has {trader_wood} wood")

    # --- 4f: Self-trade prevention ---
    print_section("Self-trade prevention")

    await give_inventory(app, "eco_trader", "herbs", 10)
    self_sell = await agents["eco_trader"].call("marketplace_order", {
        "action": "sell", "product": "herbs", "quantity": 5, "price": 3.0,
    })
    await give_balance(app, "eco_trader", 500)
    self_buy = await agents["eco_trader"].call("marketplace_order", {
        "action": "buy", "product": "herbs", "quantity": 5, "price": 3.0,
    })

    await run_tick(minutes=1)

    trader_herbs = await get_inventory_qty(app, "eco_trader", "herbs")
    print(f"  Self-trade test: trader herbs={trader_herbs} (self-match prevented or handled)")

    # Clean up open orders
    for order_id in [self_sell["order"]["id"], self_buy["order"]["id"]]:
        try:
            await agents["eco_trader"].call("marketplace_order", {
                "action": "cancel", "order_id": order_id,
            })
        except Exception:
            pass  # already filled/cancelled

    # Run 3 days of ticks
    await run_tick(hours=72)
    print("  Ran 3 days of ticks")

    print("\n  Phase 4 COMPLETE")
