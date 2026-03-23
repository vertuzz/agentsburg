"""Section 15: Business inventory transfer edge cases."""

from __future__ import annotations

from tests.helpers import TestAgent
from tests.conftest import give_balance, give_inventory, get_inventory_qty


async def run_business_transfers(client, app, clock, agents):
    """
    Section 15: Business Inventory Transfer -- Edge Cases

    Returns updated agents dict.
    """

    # ===================================================================
    # 15. Business Inventory Transfer -- Edge Cases
    # ===================================================================
    print(f"\n{'='*60}")
    print("15. BUSINESS INVENTORY TRANSFER ADVERSARIAL")
    print(f"{'='*60}")

    # Setup: create two agents, one with a business
    transfer_owner = await TestAgent.signup(client, "transfer_owner")
    transfer_other = await TestAgent.signup(client, "transfer_other")
    await give_balance(app, "transfer_owner", 1000)
    await give_balance(app, "transfer_other", 500)
    await transfer_owner.call("rent_housing", {"zone": "outskirts"})
    await transfer_other.call("rent_housing", {"zone": "outskirts"})
    biz = await transfer_owner.call("register_business", {
        "name": "Transfer Test Biz", "type": "mill", "zone": "industrial",
    })
    biz_id = biz["business_id"]

    # 15a: Transfer to business you don't own
    await give_inventory(app, "transfer_other", "wheat", 10)
    _, err = await transfer_other.try_call("business_inventory", {
        "action": "deposit", "business_id": biz_id, "good": "wheat", "quantity": 5,
    })
    assert err == "NOT_FOUND", f"Expected NOT_FOUND for non-owner, got {err}"
    print("  PASSED: Cannot deposit to business you don't own")

    # 15b: Deposit more than agent has
    await give_inventory(app, "transfer_owner", "wheat", 5)
    _, err = await transfer_owner.try_call("business_inventory", {
        "action": "deposit", "business_id": biz_id, "good": "wheat", "quantity": 999,
    })
    assert err == "INSUFFICIENT_INVENTORY"
    print("  PASSED: Cannot deposit more than owned")

    # 15c: Withdraw more than business has
    _, err = await transfer_owner.try_call("business_inventory", {
        "action": "withdraw", "business_id": biz_id, "good": "wheat", "quantity": 999,
    })
    assert err == "INSUFFICIENT_INVENTORY"
    print("  PASSED: Cannot withdraw more than business has")

    # 15d: Invalid action
    _, err = await transfer_owner.try_call("business_inventory", {
        "action": "steal", "business_id": biz_id, "good": "wheat", "quantity": 1,
    })
    assert err == "INVALID_PARAMS"
    print("  PASSED: Invalid action rejected")

    # 15e: Cooldown enforced
    clock.advance(31)  # clear any prior cooldown
    await transfer_owner.call("business_inventory", {
        "action": "deposit", "business_id": biz_id, "good": "wheat", "quantity": 3,
    })
    _, err = await transfer_owner.try_call("business_inventory", {
        "action": "withdraw", "business_id": biz_id, "good": "wheat", "quantity": 1,
    })
    assert err == "COOLDOWN_ACTIVE"
    print("  PASSED: Transfer cooldown enforced")

    # 15f: Transfer on closed business
    clock.advance(31)
    await transfer_owner.call("manage_employees", {
        "business_id": biz_id, "action": "close_business",
    })
    _, err = await transfer_owner.try_call("business_inventory", {
        "action": "withdraw", "business_id": biz_id, "good": "wheat", "quantity": 1,
    })
    assert err == "INVALID_PARAMS"
    print("  PASSED: Cannot transfer on closed business")

    # 15g: Discard unknown good
    _, err = await transfer_owner.try_call("inventory_discard", {
        "good": "unobtainium", "quantity": 1,
    })
    assert err == "INVALID_PARAMS"
    print("  PASSED: Cannot discard unknown good")

    # 15h: Discard zero quantity
    _, err = await transfer_owner.try_call("inventory_discard", {
        "good": "wheat", "quantity": 0,
    })
    assert err == "INVALID_PARAMS"
    print("  PASSED: Cannot discard zero quantity")

    # 15i: Batch deposit rollback on failure
    rollback_owner = await TestAgent.signup(client, "rollback_owner")
    await give_balance(app, "rollback_owner", 1000)
    await rollback_owner.call("rent_housing", {"zone": "outskirts"})
    rollback_biz = await rollback_owner.call("register_business", {
        "name": "Rollback Biz", "type": "general_store", "zone": "industrial",
    })
    rollback_biz_id = rollback_biz["business_id"]

    await give_inventory(app, "rollback_owner", "wood", 5)
    # Don't give stone — batch should fail and roll back wood transfer

    clock.advance(31)
    _, err = await rollback_owner.try_call("business_inventory", {
        "action": "batch_deposit",
        "business_id": rollback_biz_id,
        "goods": [
            {"good": "wood", "quantity": 3},
            {"good": "stone", "quantity": 10},  # Don't have 10 stone
        ],
    })
    assert err is not None, "Batch with insufficient goods should fail"

    # Verify agent still has wood (rollback worked)
    qty = await get_inventory_qty(app, "rollback_owner", "wood")
    assert qty == 5, f"Wood should be unchanged after rollback, got {qty}"
    print("  PASSED: Batch deposit rolls back on partial failure")

    return agents
