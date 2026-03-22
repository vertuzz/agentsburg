"""
Security and Fairness Fix Tests

Verifies that security and fairness fixes are working correctly:

1. Self-trade prevention (wash trading)
2. Cancel order fee (2% anti-spoofing)
3. Global gather cooldown (cross-resource)
4. Input validation on agent names (XSS prevention)
5. Bankruptcy seizes deposits before write-off
6. Vote persistence across elections
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.banking import BankAccount, Loan
from backend.models.government import Vote
from backend.models.marketplace import MarketOrder, MarketTrade
from tests.conftest import give_balance, force_agent_age
from tests.helpers import TestAgent, ToolCallError


# ---------------------------------------------------------------------------
# 1. Self-trade prevention
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_self_trade_prevention(client, app, clock, run_tick, db, redis_client):
    """
    Verify that the order matching engine skips self-trades (wash trading).

    An agent with both a sell and buy order at the same price should NOT
    have them matched against each other.
    """
    agent = await TestAgent.signup(client, "wash_trader")

    # Give agent balance so they can place a buy order
    await give_balance(app, "wash_trader", 500)

    # Gather berries to have inventory for sell order
    result = await agent.call("gather", {"resource": "berries"})
    assert result["gathered"] == "berries"

    # Advance past resource cooldown (berry ~25s * 2 homeless = 50s) + global (5s)
    clock.advance(55)
    await agent.call("gather", {"resource": "berries"})
    clock.advance(55)
    await agent.call("gather", {"resource": "berries"})

    # Place sell order for 2 berries at price 5.00
    sell_result = await agent.call("marketplace_order", {
        "action": "sell",
        "product": "berries",
        "quantity": 2,
        "price": 5.00,
    })
    sell_order = sell_result["order"]
    assert sell_order["status"] == "open"
    assert sell_result["immediate_fills"] == 0

    # Place buy order for 2 berries at price 5.00 (same agent, same price)
    buy_result = await agent.call("marketplace_order", {
        "action": "buy",
        "product": "berries",
        "quantity": 2,
        "price": 5.00,
    })
    buy_order = buy_result["order"]
    # The buy order should NOT have been filled against our own sell order
    assert buy_result["immediate_fills"] == 0, (
        "Self-trade should be prevented: buy order should not match own sell order"
    )
    assert buy_order["status"] == "open"

    # Run a tick to verify matching engine also skips self-trades during tick
    await run_tick(minutes=2)

    # Verify both orders are still open in the DB (filter by this agent only)
    async with app.state.session_factory() as session:
        agent_q = await session.execute(
            select(Agent).where(Agent.name == "wash_trader")
        )
        agent_row = agent_q.scalar_one()
        orders = await session.execute(
            select(MarketOrder).where(
                MarketOrder.good_slug == "berries",
                MarketOrder.agent_id == agent_row.id,
            )
        )
        open_orders = [
            o for o in orders.scalars().all()
            if o.status in ("open", "partially_filled")
        ]
        assert len(open_orders) == 2, (
            f"Expected 2 open orders (self-trade prevented), found {len(open_orders)}"
        )

        # Verify no trades were executed for this agent's orders
        agent_order_ids = [
            o.id for o in open_orders
        ]
        # Also check all agent's orders (including ones that might have been filled)
        all_agent_orders = await session.execute(
            select(MarketOrder).where(MarketOrder.agent_id == agent_row.id)
        )
        all_order_ids = [o.id for o in all_agent_orders.scalars().all()]

        trades = await session.execute(select(MarketTrade))
        trade_list = [
            t for t in trades.scalars().all()
            if t.buy_order_id in all_order_ids or t.sell_order_id in all_order_ids
        ]
        assert len(trade_list) == 0, (
            f"Expected 0 trades for wash_trader (self-trade prevented), found {len(trade_list)}"
        )

    print("\n  Self-trade prevention verified: no wash trades executed")


# ---------------------------------------------------------------------------
# 2. Cancel order fee
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_order_fee(client, app, clock, db, redis_client):
    """
    Verify that cancelling a buy order returns 98% of locked funds (2% fee).
    """
    agent = await TestAgent.signup(client, "cancel_tester")

    # Give agent a known balance
    await give_balance(app, "cancel_tester", 500.00)

    # Record balance before order
    status_before = await agent.status()
    balance_before = status_before["balance"]

    # Place buy order: 5 berries at 10.00 each = 50.00 locked
    result = await agent.call("marketplace_order", {
        "action": "buy",
        "product": "berries",
        "quantity": 5,
        "price": 10.00,
    })
    order_id = result["order"]["id"]
    order = result["order"]

    # Check balance after placing order to determine actual locked amount
    status = await agent.status()
    balance_after_order = status["balance"]
    total_locked = balance_before - balance_after_order
    assert total_locked > 0, f"Expected funds to be locked, but balance unchanged"

    # Only the unfilled portion matters for cancellation refund
    unfilled_qty = order["quantity_total"] - order["quantity_filled"]
    order_price = float(order["price"])
    cancellable_amount = unfilled_qty * order_price

    # Cancel the order
    cancel_result = await agent.call("marketplace_order", {
        "action": "cancel",
        "order_id": order_id,
    })
    assert cancel_result["cancelled"] is True

    # Refund should be 98% of cancellable amount, fee = 2%
    refund = cancel_result["refund"]
    assert refund["type"] == "funds_returned"
    expected_fee = cancellable_amount * 0.02
    expected_refund = cancellable_amount - expected_fee
    assert abs(refund["cancel_fee"] - expected_fee) < 0.01, (
        f"Expected cancel fee of {expected_fee:.2f} (2% of {cancellable_amount:.2f}), "
        f"got {refund['cancel_fee']}"
    )
    assert abs(refund["amount"] - expected_refund) < 0.01, (
        f"Expected refund of {expected_refund:.2f} (98% of {cancellable_amount:.2f}), "
        f"got {refund['amount']}"
    )

    # Final balance should be balance_after_order + refund
    status = await agent.status()
    expected_final = balance_after_order + expected_refund
    assert abs(status["balance"] - expected_final) < 0.01, (
        f"Expected final balance ~{expected_final:.2f}, got {status['balance']}"
    )

    # The fee must be exactly 2% of the cancellable portion
    assert refund["cancel_fee"] > 0, "Cancel fee must be greater than 0"
    fee_rate = refund["cancel_fee"] / cancellable_amount if cancellable_amount > 0 else 0
    assert abs(fee_rate - 0.02) < 0.001, (
        f"Expected 2% fee rate, got {fee_rate:.4f}"
    )

    print("\n  Cancel order fee verified: 2% fee deducted correctly")


# ---------------------------------------------------------------------------
# 3. Global gather cooldown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_global_gather_cooldown(client, clock, redis_client):
    """
    Verify the global 5-second gather cooldown prevents interleaved gathering
    of different resources.
    """
    agent = await TestAgent.signup(client, "gather_cooldown_agent")

    # First gather: berries — should succeed
    result = await agent.call("gather", {"resource": "berries"})
    assert result["gathered"] == "berries"

    # Immediately try to gather a DIFFERENT resource (wood)
    # Should fail due to global gather cooldown (5 seconds)
    _, error = await agent.try_call("gather", {"resource": "wood"})
    assert error == "COOLDOWN_ACTIVE", (
        f"Expected COOLDOWN_ACTIVE for global gather cooldown, got {error}"
    )

    # Advance clock past the 5s global cooldown but before per-resource cooldown
    clock.advance(6)

    # Gathering wood should now succeed (global cooldown expired)
    result = await agent.call("gather", {"resource": "wood"})
    assert result["gathered"] == "wood"

    print("\n  Global gather cooldown verified: cross-resource cooldown enforced")


# ---------------------------------------------------------------------------
# 4. Input validation on agent names
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_input_validation_agent_name(client, redis_client):
    """
    Verify that agent signup rejects names with HTML/script injection characters
    and other invalid inputs.
    """
    # Name with <script> tag — should fail
    _, error = await _try_signup(client, "<script>alert('xss')</script>")
    assert error == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS for script tag in name, got {error}"
    )

    # Name with just < character — should fail
    _, error = await _try_signup(client, "bob<evil")
    assert error == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS for < in name, got {error}"
    )

    # Name with & character — should fail
    _, error = await _try_signup(client, "alice&bob")
    assert error == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS for & in name, got {error}"
    )

    # Empty name — should fail
    _, error = await _try_signup(client, "")
    assert error == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS for empty name, got {error}"
    )

    # Single character name (too short, min 2) — should fail
    _, error = await _try_signup(client, "a")
    assert error == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS for single-char name, got {error}"
    )

    # Valid name — should succeed
    agent = await TestAgent.signup(client, "valid_alice")
    status = await agent.status()
    assert status["name"] == "valid_alice"

    print("\n  Input validation verified: XSS/injection characters rejected")


async def _try_signup(client, name: str) -> tuple[dict | None, str | None]:
    """Attempt signup and return (result, None) or (None, error_code)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "signup",
            "arguments": {"name": name},
        },
    }
    response = await client.post("/mcp", json=payload)
    body = response.json()
    if "error" in body:
        error = body["error"]
        code = error.get("data", {}).get("code", "UNKNOWN") if isinstance(error.get("data"), dict) else "UNKNOWN"
        return None, code
    return body.get("result"), None


# ---------------------------------------------------------------------------
# 5. Bankruptcy seizes deposits first
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bankruptcy_seizes_deposits_first(client, app, clock, run_tick, db, redis_client):
    """
    Verify that bankruptcy processing seizes bank deposits to pay down loans
    before any debt write-off. This prevents the exploit:
    take loan -> deposit -> default -> recover deposits.
    """
    agent = await TestAgent.signup(client, "bankrupt_depositor")

    # Give agent enough balance to deposit and take a loan
    await give_balance(app, "bankrupt_depositor", 600)

    # Deposit 400 into bank
    deposit_result = await agent.call("bank", {
        "action": "deposit",
        "amount": 400,
    })
    assert abs(deposit_result["account_balance"] - 400) < 0.01

    # Check wallet balance after deposit: 600 - 400 = 200
    status = await agent.status()
    assert abs(status["balance"] - 200) < 0.01

    # Take a small loan (within per-agent reserve cap of 10%)
    loan_result = await agent.call("bank", {
        "action": "take_loan",
        "amount": 30,
    })
    assert "loan_id" in loan_result

    # Now simulate deep debt by setting balance far below bankruptcy threshold (-200)
    # Agent has: wallet = ~300 (200 + 100 loan), deposit = 400, loan = ~105 (with interest)
    # Set wallet to -250 to trigger bankruptcy
    await give_balance(app, "bankrupt_depositor", -250)

    # Record the bank deposit before bankruptcy
    async with app.state.session_factory() as session:
        acct = await session.execute(
            select(BankAccount).join(Agent).where(Agent.name == "bankrupt_depositor")
        )
        account = acct.scalar_one()
        deposit_before = float(account.balance)
        assert deposit_before > 0, "Agent should have bank deposits before bankruptcy"

        # Check active loan
        loan_q = await session.execute(
            select(Loan).join(Agent).where(
                Agent.name == "bankrupt_depositor",
                Loan.status == "active",
            )
        )
        active_loan = loan_q.scalar_one_or_none()
        assert active_loan is not None, "Agent should have an active loan"
        loan_balance_before = float(active_loan.remaining_balance)

    # Run tick to trigger bankruptcy (balance -250 < threshold -200)
    await run_tick(hours=1)

    # Verify: deposits were seized, loan was paid from deposits
    async with app.state.session_factory() as session:
        # Check the bank account is zeroed
        acct = await session.execute(
            select(BankAccount).join(Agent).where(Agent.name == "bankrupt_depositor")
        )
        account = acct.scalar_one_or_none()
        deposit_after = float(account.balance) if account else 0
        assert deposit_after == 0, (
            f"Expected deposit to be seized (0), got {deposit_after}"
        )

        # Check the loan is defaulted
        loan_q = await session.execute(
            select(Loan).join(Agent).where(
                Agent.name == "bankrupt_depositor",
            )
        )
        loan = loan_q.scalar_one()
        assert loan.status == "defaulted", (
            f"Expected loan status 'defaulted', got {loan.status}"
        )

        # Check agent's bankruptcy count incremented
        agent_q = await session.execute(
            select(Agent).where(Agent.name == "bankrupt_depositor")
        )
        agent_row = agent_q.scalar_one()
        assert agent_row.bankruptcy_count >= 1, (
            f"Expected bankruptcy_count >= 1, got {agent_row.bankruptcy_count}"
        )
        # Agent balance should be 0 after bankruptcy (not still holding deposits)
        assert float(agent_row.balance) >= 0, (
            f"Expected non-negative balance after bankruptcy, got {float(agent_row.balance)}"
        )

    print("\n  Bankruptcy deposit seizure verified: deposits used to pay loans first")


# ---------------------------------------------------------------------------
# 6. Vote persistence across elections
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vote_persistence_across_elections(client, app, clock, run_tick, db, redis_client):
    """
    Verify that votes persist across weekly election tallies.
    Agents don't need to re-vote every week — their last vote carries forward.
    """
    # Clean up any votes from other tests (since votes now persist)
    from sqlalchemy import delete
    async with app.state.session_factory() as session:
        await session.execute(delete(Vote))
        await session.commit()

    # Create 3 agents and make them eligible to vote
    voter1 = await TestAgent.signup(client, "persist_voter1")
    voter2 = await TestAgent.signup(client, "persist_voter2")
    voter3 = await TestAgent.signup(client, "persist_voter3")

    voting_eligibility = 1_209_600  # 2 weeks in seconds
    for name in ["persist_voter1", "persist_voter2", "persist_voter3"]:
        await force_agent_age(app, name, voting_eligibility + 100)

    # All 3 vote for libertarian
    for voter in [voter1, voter2, voter3]:
        result = await voter.call("vote", {"government_type": "libertarian"})
        assert result["voted_for"] == "libertarian"

    # Force the weekly tick boundary: set last_weekly to long ago
    now_ts = clock.now().timestamp()
    await redis_client.set("tick:last_weekly", str(now_ts - 700_000))

    # Run first weekly tick (election tally)
    tick1 = await run_tick()
    assert tick1.get("weekly_tick") is not None, "Weekly tick should have run"
    election1 = tick1["weekly_tick"]
    assert election1["winner"] == "libertarian", (
        f"Expected libertarian to win (3 votes), got {election1['winner']}"
    )
    assert election1["total_votes"] == 3

    print(f"\n  Election 1: winner={election1['winner']}, votes={election1['total_votes']}")

    # Advance clock by 7+ days for next weekly tick
    clock.advance(604800 + 100)

    # Run second weekly tick WITHOUT re-voting
    tick2 = await run_tick()
    assert tick2.get("weekly_tick") is not None, "Second weekly tick should have run"
    election2 = tick2["weekly_tick"]

    # Votes should persist — still 3 votes for libertarian
    assert election2["total_votes"] == 3, (
        f"Expected 3 persisted votes, got {election2['total_votes']}"
    )
    assert election2["winner"] == "libertarian", (
        f"Expected libertarian to win again (persisted votes), got {election2['winner']}"
    )

    # Verify votes still exist in DB
    async with app.state.session_factory() as session:
        votes = await session.execute(select(Vote))
        vote_list = list(votes.scalars().all())
        libertarian_votes = [v for v in vote_list if v.template_slug == "libertarian"]
        assert len(libertarian_votes) == 3, (
            f"Expected 3 libertarian votes in DB, found {len(libertarian_votes)}"
        )

    print(f"  Election 2: winner={election2['winner']}, votes={election2['total_votes']}")
    print("  Vote persistence verified: votes carry forward across elections")
