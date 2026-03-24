"""Sections 3-5: Concurrency and double-spend prevention."""

from __future__ import annotations

import asyncio

from tests.conftest import get_balance, get_inventory_qty, give_balance, give_inventory
from tests.helpers import TestAgent


async def run_concurrency(client, app, clock, agents):
    """
    Section 3: Concurrency - Double-Spend Prevention (Balance)
    Section 4: Concurrency - Double-Spend Prevention (Inventory)
    Section 5: Concurrency - TOCTOU on Trade Response

    Returns updated agents dict.
    """

    # ===================================================================
    # Section 3: Concurrency - Double-Spend Prevention (Balance)
    # ===================================================================
    print("\n--- Section 3: Concurrency - Double-Spend Prevention (Balance) ---")

    adv_spender = await TestAgent.signup(client, "adv_spender")
    adv_target1 = await TestAgent.signup(client, "adv_target1")
    await give_balance(app, "adv_spender", 100)
    agents["adv_spender"] = adv_spender
    agents["adv_target1"] = adv_target1

    # Two concurrent trades each offering 80 money
    async def propose_money_trade(target_name):
        return await adv_spender.try_call(
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
        propose_money_trade("adv_target1"),
        propose_money_trade("adv_valid"),
        return_exceptions=True,
    )

    successes = sum(1 for r in results if not isinstance(r, Exception) and r[1] is None)
    failures = sum(1 for r in results if not isinstance(r, Exception) and r[1] is not None)

    # At most 1 should succeed (cannot spend 160 from 100)
    assert successes <= 1, f"Double-spend: {successes} trades succeeded with only 100 balance"

    # Verify balance never went negative
    bal = await get_balance(app, "adv_spender")
    assert bal >= 0, f"Balance went negative: {bal}"

    print(f"  PASSED: {successes} succeeded, {failures} failed, balance={bal}")

    # ===================================================================
    # Section 4: Concurrency - Double-Spend Prevention (Inventory)
    # ===================================================================
    print("\n--- Section 4: Concurrency - Double-Spend Prevention (Inventory) ---")

    adv_inv_spender = await TestAgent.signup(client, "adv_inv_spender")
    await TestAgent.signup(client, "adv_inv_tgt1")
    await TestAgent.signup(client, "adv_inv_tgt2")
    await give_inventory(app, "adv_inv_spender", "berries", 5)
    await give_balance(app, "adv_inv_spender", 100)

    async def propose_item_trade(agent, target_name):
        return await agent.try_call(
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
        propose_item_trade(adv_inv_spender, "adv_inv_tgt1"),
        propose_item_trade(adv_inv_spender, "adv_inv_tgt2"),
        return_exceptions=True,
    )

    successes = sum(1 for r in results if not isinstance(r, Exception) and r[1] is None)

    assert successes <= 1, f"Inventory double-spend: {successes} trades succeeded with only 5 berries"

    inv_qty = await get_inventory_qty(app, "adv_inv_spender", "berries")
    assert inv_qty >= 0, f"Inventory went negative: {inv_qty}"

    print(f"  PASSED: {successes} item trades succeeded, remaining berries={inv_qty}")

    # ===================================================================
    # Section 5: Concurrency - TOCTOU on Trade Response
    # ===================================================================
    print("\n--- Section 5: Concurrency - TOCTOU on Trade Response ---")

    adv_proposer1 = await TestAgent.signup(client, "adv_proposer1")
    adv_proposer2 = await TestAgent.signup(client, "adv_proposer2")
    adv_toctou_target = await TestAgent.signup(client, "adv_toctou_tgt")

    await give_balance(app, "adv_proposer1", 100)
    await give_balance(app, "adv_proposer2", 100)
    await give_inventory(app, "adv_toctou_tgt", "berries", 5)
    await give_balance(app, "adv_toctou_tgt", 100)

    # Each proposer proposes a trade requesting 4 berries from target
    trade1_result = await adv_proposer1.call(
        "trade",
        {
            "action": "propose",
            "target_agent": "adv_toctou_tgt",
            "offer_items": [],
            "request_items": [{"good_slug": "berries", "quantity": 4}],
            "offer_money": 10,
            "request_money": 0,
        },
    )
    trade1_id = trade1_result["trade"]["id"]

    trade2_result = await adv_proposer2.call(
        "trade",
        {
            "action": "propose",
            "target_agent": "adv_toctou_tgt",
            "offer_items": [],
            "request_items": [{"good_slug": "berries", "quantity": 4}],
            "offer_money": 10,
            "request_money": 0,
        },
    )
    trade2_id = trade2_result["trade"]["id"]

    # Target tries to accept both concurrently
    accept_results = await asyncio.gather(
        adv_toctou_target.try_call(
            "trade",
            {
                "action": "respond",
                "trade_id": trade1_id,
                "accept": True,
            },
        ),
        adv_toctou_target.try_call(
            "trade",
            {
                "action": "respond",
                "trade_id": trade2_id,
                "accept": True,
            },
        ),
        return_exceptions=True,
    )

    accept_successes = sum(1 for r in accept_results if not isinstance(r, Exception) and r[1] is None)

    assert accept_successes <= 1, (
        f"TOCTOU: {accept_successes} trade accepts succeeded, but target only had 5 berries for 2x4 requests"
    )

    target_berries = await get_inventory_qty(app, "adv_toctou_tgt", "berries")
    assert target_berries >= 0, f"Target inventory went negative: {target_berries}"

    print(f"  PASSED: {accept_successes} accepts succeeded, target berries={target_berries}")

    return agents
