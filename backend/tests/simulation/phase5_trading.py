"""Phase 5: Direct Trading & Messaging (Days 8-10) — propose, accept, reject, cancel, messaging."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.conftest import get_balance, get_inventory_qty, give_balance, give_inventory
from tests.simulation.helpers import print_phase, print_section

if TYPE_CHECKING:
    from tests.helpers import TestAgent


async def run_phase_5(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Test direct trades (propose/accept/reject/cancel) and messaging."""
    print_phase(5, "DIRECT TRADING & MESSAGING")

    agent_a = agents["eco_gatherer1"]
    agent_b = agents["eco_gatherer2"]

    # Setup for trading
    await give_balance(app, "eco_gatherer1", 200)
    await give_balance(app, "eco_gatherer2", 200)
    await give_inventory(app, "eco_gatherer1", "berries", 20)
    await give_inventory(app, "eco_gatherer2", "wood", 15)

    # --- 5a: Propose and accept a trade ---
    print_section("Propose and accept trade")

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
    trade_id_1 = propose["trade"]["id"]
    assert propose["trade"]["status"] == "pending"
    print("  Trade proposed: A offers 8 berries + 5 money for 4 wood")

    # Verify escrow locked
    a_berries = await get_inventory_qty(app, "eco_gatherer1", "berries")
    assert a_berries == 12, f"A should have 12 berries (8 escrowed), has {a_berries}"
    a_bal = await get_balance(app, "eco_gatherer1")
    assert float(a_bal) < 200, "A's balance should be reduced by escrow"
    print(f"  Escrow locked: A has {a_berries} berries, balance={float(a_bal):.2f}")

    # B accepts
    accept = await agent_b.call(
        "trade",
        {
            "action": "respond",
            "trade_id": trade_id_1,
            "accept": True,
        },
    )
    assert accept["status"] == "accepted"

    # Verify exchange
    a_wood = await get_inventory_qty(app, "eco_gatherer1", "wood")
    b_berries = await get_inventory_qty(app, "eco_gatherer2", "berries")
    assert a_wood >= 4, f"A should have received 4 wood, has {a_wood}"
    assert b_berries >= 8, f"B should have received 8 berries, has {b_berries}"
    print(f"  Trade accepted: A got {a_wood} wood, B got {b_berries} berries")

    # --- 5b: Propose and reject a trade ---
    print_section("Propose and reject trade")

    await give_inventory(app, "eco_gatherer1", "berries", 10)
    berries_before_reject = await get_inventory_qty(app, "eco_gatherer1", "berries")
    await get_balance(app, "eco_gatherer1")

    propose2 = await agent_a.call(
        "trade",
        {
            "action": "propose",
            "target_agent": "eco_gatherer2",
            "offer_items": [{"good_slug": "berries", "quantity": 3}],
            "request_items": [{"good_slug": "wood", "quantity": 2}],
            "offer_money": 0.0,
            "request_money": 0.0,
        },
    )
    trade_id_2 = propose2["trade"]["id"]

    reject = await agent_b.call(
        "trade",
        {
            "action": "respond",
            "trade_id": trade_id_2,
            "accept": False,
        },
    )
    assert reject["status"] == "rejected"

    # Verify escrow returned
    berries_after_reject = await get_inventory_qty(app, "eco_gatherer1", "berries")
    assert berries_after_reject == berries_before_reject, (
        f"Berries should be returned: before={berries_before_reject}, after={berries_after_reject}"
    )
    print(f"  Trade rejected, escrow returned: berries={berries_after_reject}")

    # --- 5c: Propose and cancel ---
    print_section("Propose and cancel trade")

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
    trade_id_3 = propose3["trade"]["id"]

    cancel_trade = await agent_a.call(
        "trade",
        {
            "action": "cancel",
            "trade_id": trade_id_3,
        },
    )
    assert cancel_trade["status"] == "cancelled"

    stone_after = await get_inventory_qty(app, "eco_gatherer1", "stone")
    assert stone_after == stone_before, f"Stone should be returned: before={stone_before}, after={stone_after}"
    print(f"  Trade cancelled, escrow returned: stone={stone_after}")

    # --- 5d: Messaging ---
    print_section("Messaging")

    send_result = await agents["eco_trader"].call(
        "messages",
        {
            "action": "send",
            "to_agent": "eco_baker",
            "text": "I have 20 berries to sell. Interested?",
        },
    )
    assert "message_id" in send_result or "sent" in str(send_result).lower()
    print("  Message sent: trader -> baker")

    read_result = await agents["eco_baker"].call(
        "messages",
        {
            "action": "read",
        },
    )
    assert "messages" in read_result
    msgs = read_result["messages"]
    assert len(msgs) > 0, "Baker should have at least one message"
    assert any("berries" in m.get("text", "") for m in msgs)
    print(f"  Baker read {len(msgs)} messages, found berries offer")

    # Run 2 days
    await run_tick(hours=48)

    print("\n  Phase 5 COMPLETE")
