"""
Feedback Fixes Test — NW calculation, early repayment, tick health, job improvements.

Covers all changes from the Meridian Rex feedback session:

1. Net worth subtracts loan liability
2. Net worth includes business inventory
3. Net worth includes locked sell order value
4. Early loan repayment (repay_loan action)
5. Tick health endpoint (economy section=tick_status)
6. Self-employment blocked (can't apply to own business job)
7. Job listings include employer_balance and estimated_pay_cycles

3 agents, full E2E through real REST API. Only mock: MockClock.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.banking import CentralBank
from tests.conftest import force_agent_age, give_balance, give_inventory
from tests.helpers import TestAgent


@pytest.mark.asyncio
async def test_feedback_fixes(client, app, clock, run_tick, redis_client):
    """Comprehensive test for all player feedback fixes."""

    # ======================================================================
    # Setup: 3 agents with housing and businesses
    # ======================================================================
    print("\n--- Setup: agents ---")

    alice = await TestAgent.signup(client, "fb_alice")
    bob = await TestAgent.signup(client, "fb_bob")
    carol = await TestAgent.signup(client, "fb_carol")

    # Give them enough money to work with
    await give_balance(app, "fb_alice", 5000)
    await give_balance(app, "fb_bob", 5000)
    await give_balance(app, "fb_carol", 5000)

    # Age them past the starter loan window
    await force_agent_age(app, "fb_alice", 7200)
    await force_agent_age(app, "fb_bob", 7200)
    await force_agent_age(app, "fb_carol", 7200)

    # Run initial tick to bootstrap economy
    await run_tick(minutes=2)

    # Get housing
    await alice.call("rent_housing", {"zone": "industrial"})
    await bob.call("rent_housing", {"zone": "industrial"})
    await carol.call("rent_housing", {"zone": "suburbs"})

    # Alice registers a farm (for business inventory test)
    alice_biz = await alice.call(
        "register_business",
        {
            "name": "Alice Farm",
            "type": "farm",
            "zone": "industrial",
        },
    )
    alice_biz_id = alice_biz["business_id"]

    # Configure production and give inventory to the business
    await alice.call(
        "configure_production",
        {
            "business_id": alice_biz_id,
            "product": "flour",
        },
    )

    # Give Alice some wheat to deposit into business
    await give_inventory(app, "fb_alice", "wheat", 50)
    await alice.call(
        "business_inventory",
        {
            "business_id": alice_biz_id,
            "action": "deposit",
            "good": "wheat",
            "quantity": 50,
        },
    )

    # Ensure central bank has reserves
    async with app.state.session_factory() as session:
        cb = (await session.execute(select(CentralBank).where(CentralBank.id == 1))).scalar_one()
        if float(cb.reserves) < 50000:
            cb.reserves = Decimal("100000")
            await session.commit()

    print("  Setup complete: 3 agents, Alice has a farm with 50 wheat")

    # ======================================================================
    # Test 1: Net worth includes business inventory
    # ======================================================================
    print("\n--- Test 1: Business inventory in NW ---")

    leaderboard = await alice.call("leaderboard")
    alice_entry = next(e for e in leaderboard["leaderboard"] if e["agent_name"] == "fb_alice")
    assert alice_entry["net_worth"] > 0

    # Also check credit components
    balance_view = await alice.call("bank", {"action": "view_balance"})
    components = balance_view["credit"]["components"]
    assert components["business_inventory_value"] > 0, (
        f"Business inventory should be > 0, got {components['business_inventory_value']}"
    )
    print(f"  Business inventory value in credit: {components['business_inventory_value']}")

    # ======================================================================
    # Test 2: Net worth subtracts loan liability
    # ======================================================================
    print("\n--- Test 2: Loan subtracted from NW ---")

    # Record NW before loan
    lb_before = await bob.call("leaderboard")
    bob_before = next(e for e in lb_before["leaderboard"] if e["agent_name"] == "fb_bob")
    nw_before = bob_before["net_worth"]

    # Bob takes a loan
    loan = await bob.call("bank", {"action": "take_loan", "amount": 500})
    assert loan["principal"] == 500.0
    loan_id = loan["loan_id"]

    # Check NW after loan — should NOT increase by the full loan amount
    # because the loan liability offsets the cash received
    lb_after = await bob.call("leaderboard")
    bob_after = next(e for e in lb_after["leaderboard"] if e["agent_name"] == "fb_bob")
    nw_after = bob_after["net_worth"]

    # The NW change should be approximately 0 (cash + loan_principal - loan_liability)
    # Loan remaining_balance = principal * (1 + interest_rate), which is > principal,
    # so NW should actually DECREASE slightly
    nw_change = nw_after - nw_before
    assert nw_change <= 0, (
        f"Taking a loan should not increase NW. Before: {nw_before}, After: {nw_after}, Change: {nw_change}"
    )

    # Verify loan_liability appears in credit components
    bob_credit = await bob.call("bank", {"action": "view_balance"})
    assert bob_credit["credit"]["components"]["loan_liability"] > 0, "Loan liability should be > 0 after taking a loan"
    print(f"  NW before loan: {nw_before}, after: {nw_after}, change: {nw_change:.2f}")
    print(f"  Loan liability in credit: {bob_credit['credit']['components']['loan_liability']}")

    # ======================================================================
    # Test 3: Net worth includes locked sell order value
    # ======================================================================
    print("\n--- Test 3: Sell orders in NW ---")

    # Give Carol some berries
    await give_inventory(app, "fb_carol", "berries", 30)

    # Record NW before sell order
    lb_pre_sell = await carol.call("leaderboard")
    carol_pre = next(e for e in lb_pre_sell["leaderboard"] if e["agent_name"] == "fb_carol")
    nw_pre_sell = carol_pre["net_worth"]

    # Place a sell order
    await carol.call(
        "marketplace_order",
        {
            "action": "sell",
            "product": "berries",
            "quantity": 20,
            "price": 5.0,
        },
    )

    # NW should stay approximately the same (locked goods still count)
    lb_post_sell = await carol.call("leaderboard")
    carol_post = next(e for e in lb_post_sell["leaderboard"] if e["agent_name"] == "fb_carol")
    nw_post_sell = carol_post["net_worth"]

    # Should be within a small margin (base_value might differ from sell price)
    nw_diff = abs(nw_post_sell - nw_pre_sell)
    assert nw_diff < 5.0, (
        f"Sell order should not significantly change NW. Before: {nw_pre_sell}, After: {nw_post_sell}, Diff: {nw_diff}"
    )

    # Verify locked_sell_value in credit
    carol_credit = await carol.call("bank", {"action": "view_balance"})
    assert carol_credit["credit"]["components"]["locked_sell_value"] > 0, (
        "locked_sell_value should be > 0 with an open sell order"
    )
    print(f"  NW before sell order: {nw_pre_sell}, after: {nw_post_sell}, diff: {nw_diff:.2f}")
    print(f"  Locked sell value: {carol_credit['credit']['components']['locked_sell_value']}")

    # ======================================================================
    # Test 4: Early loan repayment
    # ======================================================================
    print("\n--- Test 4: Early loan repayment ---")

    # Bob repays his loan early
    bob_balance_before = (await bob.status())["balance"]
    repay = await bob.call("bank", {"action": "repay_loan"})
    assert repay["action"] == "repay_loan"
    assert repay["loan_id"] == loan_id
    assert repay["amount_repaid"] > 0
    assert repay["wallet_balance"] < bob_balance_before

    # Verify loan is paid off
    bob_credit_after = await bob.call("bank", {"action": "view_balance"})
    assert bob_credit_after["credit"]["components"]["loan_liability"] == 0, "Loan liability should be 0 after repayment"

    # Verify Bob can take a new loan now
    new_loan = await bob.call("bank", {"action": "take_loan", "amount": 100})
    assert new_loan["principal"] == 100.0
    print(f"  Repaid: {repay['amount_repaid']:.2f}, new balance: {repay['wallet_balance']:.2f}")
    print(f"  New loan taken after repayment: principal={new_loan['principal']}")

    # Repay loan with insufficient funds should fail
    await give_balance(app, "fb_bob", 0.01)
    _, err = await bob.try_call("bank", {"action": "repay_loan"})
    assert err == "INSUFFICIENT_FUNDS", f"Expected INSUFFICIENT_FUNDS, got {err}"
    print("  Insufficient funds repayment correctly rejected")

    # No active loan should fail
    # First restore balance and repay
    await give_balance(app, "fb_bob", 50000)
    await bob.call("bank", {"action": "repay_loan"})
    _, err2 = await bob.try_call("bank", {"action": "repay_loan"})
    assert err2 == "NOT_ELIGIBLE", f"Expected NOT_ELIGIBLE for no active loan, got {err2}"
    print("  No active loan correctly rejected")

    # ======================================================================
    # Test 5: Tick health endpoint
    # ======================================================================
    print("\n--- Test 5: Tick health endpoint ---")

    # First run a tick to populate Redis timestamps
    await run_tick(hours=1)

    tick_status = await alice.call("get_economy", {"section": "tick_status"})
    assert tick_status["section"] == "tick_status"
    assert "server_time" in tick_status
    assert "overall_healthy" in tick_status
    assert "ticks" in tick_status
    assert "slow_hourly" in tick_status["ticks"]
    assert "daily" in tick_status["ticks"]
    assert "weekly" in tick_status["ticks"]

    hourly = tick_status["ticks"]["slow_hourly"]
    assert hourly["last_run_at"] is not None, "Hourly tick should have run"
    assert hourly["healthy"] is True, "Hourly tick should be healthy"
    assert hourly["expected_interval_seconds"] == 3600
    print(f"  Tick health: overall={tick_status['overall_healthy']}")
    print(f"  Hourly: {hourly['status']}, last_run={hourly['last_run_at']}")

    # ======================================================================
    # Test 6: Self-employment blocked
    # ======================================================================
    print("\n--- Test 6: Self-employment blocked ---")

    # Alice posts a job at her farm
    job = await alice.call(
        "manage_employees",
        {
            "action": "post_job",
            "business_id": alice_biz_id,
            "title": "Farm Worker",
            "wage": 15,
            "product": "flour",
            "max_workers": 3,
        },
    )
    job_id = job["job_id"]

    # Alice tries to apply to her own job — should fail
    _, err = await alice.try_call("apply_job", {"job_id": job_id})
    assert err == "INVALID_PARAMS", f"Expected INVALID_PARAMS for self-employment, got {err}"
    print("  Self-employment correctly blocked")

    # Carol can apply (she's not the owner)
    hire_result = await carol.call("apply_job", {"job_id": job_id})
    assert hire_result["business_name"] == "Alice Farm"
    print(f"  Carol successfully hired at {hire_result['business_name']}")

    # ======================================================================
    # Test 7: Job listing improvements
    # ======================================================================
    print("\n--- Test 7: Job listing enrichments ---")

    jobs = await bob.call("list_jobs")
    assert len(jobs["items"]) > 0

    job_item = jobs["items"][0]
    assert "employer_balance" in job_item, "Job listing should include employer_balance"
    assert "estimated_pay_cycles" in job_item, "Job listing should include estimated_pay_cycles"
    assert isinstance(job_item["employer_balance"], (int, float))
    assert isinstance(job_item["estimated_pay_cycles"], int)
    assert job_item["estimated_pay_cycles"] >= 0
    print(
        f"  Job: employer_balance={job_item['employer_balance']}, estimated_pay_cycles={job_item['estimated_pay_cycles']}"
    )

    print("\n--- All feedback fix tests passed! ---")
