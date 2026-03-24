"""Finance & Law: banking, loans, government, elections, jail, deactivation.

Covers:
- Starter loan for new agents
- Credit score in status
- Deposit, withdraw, view balance
- Loan mechanics (take loan, installments, interest, second loan blocked)
- Money supply tracking
- Voting and weekly election tick
- Tax rate verification under free_market
- Tax collection on marketplace income
- Jail mechanics: comprehensive blocked/allowed tool lists
- Vote persistence across elections
- Agent deactivation after 2 bankruptcies (blocked actions, no survival charges)
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete as _delete
from sqlalchemy import func, select

from backend.models.agent import Agent
from backend.models.banking import BankAccount, CentralBank
from backend.models.government import GovernmentState, Vote
from backend.models.transaction import Transaction
from tests.conftest import force_agent_age, get_balance, give_balance, give_inventory, jail_agent
from tests.helpers import TestAgent
from tests.simulation.helpers import AGENT_NAMES, print_section, print_stage


async def run_finance_and_law(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Test banking, loans, government, elections, jail, and deactivation."""
    print_stage("FINANCE & LAW")

    # ------------------------------------------------------------------
    # Starter loan
    # ------------------------------------------------------------------
    print_section("Starter loan")

    starter = await TestAgent.signup(client, "starter_loan_agent")
    starter_status = await starter.status()
    assert starter_status["max_loan_amount"] >= 75.0, (
        f"New agent should qualify for starter loan >= 75, got {starter_status['max_loan_amount']}"
    )

    async with app.state.session_factory() as session:
        cb = (await session.execute(select(CentralBank).where(CentralBank.id == 1))).scalar_one()
        if float(cb.reserves) < 10000:
            cb.reserves = Decimal("50000")
            await session.commit()

    starter_loan = await starter.call("bank", {"action": "take_loan", "amount": 50})
    assert starter_loan["principal"] == 50.0
    assert starter_loan["installments_remaining"] == 24
    assert starter_loan["interest_rate"] > 0
    assert starter_loan["wallet_balance"] >= 65.0  # 15 starting + 50 loan
    print(
        f"  Starter loan: principal=50, rate={starter_loan['interest_rate']}, balance={starter_loan['wallet_balance']}"
    )

    # ------------------------------------------------------------------
    # Credit score in status
    # ------------------------------------------------------------------
    print_section("Credit score")

    banker = agents["eco_banker"]
    await give_balance(app, "eco_banker", 1000)
    banker_status = await banker.status()
    assert "credit_score" in banker_status
    assert "max_loan_amount" in banker_status
    assert isinstance(banker_status["credit_score"], (int, float))
    print(f"  credit_score={banker_status['credit_score']}, max_loan={banker_status['max_loan_amount']}")

    # ------------------------------------------------------------------
    # Deposit, withdraw, view
    # ------------------------------------------------------------------
    print_section("Banking operations")

    dep = await banker.call("bank", {"action": "deposit", "amount": 300})
    assert dep["account_balance"] == 300.0
    print(f"  Deposited 300 → account={dep['account_balance']}")

    wd = await banker.call("bank", {"action": "withdraw", "amount": 100})
    assert wd["account_balance"] == 200.0
    print(f"  Withdrew 100 → account={wd['account_balance']}")

    view = await banker.call("bank", {"action": "view_balance"})
    assert "account_balance" in view
    assert "credit" in view
    print(f"  View: account={view['account_balance']}, credit_score={view['credit']['credit_score']}")

    # ------------------------------------------------------------------
    # Loan mechanics
    # ------------------------------------------------------------------
    print_section("Loan mechanics")

    async with app.state.session_factory() as session:
        cb = (await session.execute(select(CentralBank).where(CentralBank.id == 1))).scalar_one()
        if float(cb.reserves) < 10000:
            cb.reserves = Decimal("50000")
            await session.commit()

    loan = await banker.call("bank", {"action": "take_loan", "amount": 100})
    assert "principal" in loan
    assert loan["installments_remaining"] == 24
    assert loan["interest_rate"] > 0
    print(f"  Loan: principal={loan['principal']}, rate={loan['interest_rate']}")

    # Cannot take second loan
    _, err = await banker.try_call("bank", {"action": "take_loan", "amount": 50})
    assert err is not None
    print(f"  Second loan rejected (error={err})")

    # Run ticks for interest + installments
    view_before = await banker.call("bank", {"action": "view_balance"})
    acct_before = view_before["account_balance"]
    await run_tick(hours=48)
    view_after = await banker.call("bank", {"action": "view_balance"})
    acct_after = view_after["account_balance"]
    print(f"  After 48h: account {acct_before} → {acct_after}")

    active_loans = view_after.get("active_loans", [])
    if active_loans:
        remaining = active_loans[0].get("installments_remaining", 24)
        assert remaining < 24, "Some installments should have been collected"
        print(f"  Loan installments remaining: {remaining}")

    # ------------------------------------------------------------------
    # Money supply tracking
    # ------------------------------------------------------------------
    print_section("Money supply")

    async with app.state.session_factory() as session:
        wallet_total = float((await session.execute(select(func.coalesce(func.sum(Agent.balance), 0)))).scalar_one())
        bank_total = float(
            (await session.execute(select(func.coalesce(func.sum(BankAccount.balance), 0)))).scalar_one()
        )
        cb_row = (await session.execute(select(CentralBank).where(CentralBank.id == 1))).scalar_one_or_none()
        reserves = float(cb_row.reserves) if cb_row else 0.0
    print(f"  Wallets: {wallet_total:.2f}, Bank: {bank_total:.2f}, Reserves: {reserves:.2f}")
    print(f"  Trackable total: {wallet_total + bank_total + reserves:.2f}")

    await run_tick(hours=48)

    # ------------------------------------------------------------------
    # Voting and elections
    # ------------------------------------------------------------------
    print_section("Voting and elections")

    # Clean votes, age agents for eligibility
    async with app.state.session_factory() as session:
        await session.execute(_delete(Vote))
        await session.commit()

    VOTE_AGE = 1209600 + 100  # 2 weeks + buffer
    for name in AGENT_NAMES:
        await force_agent_age(app, name, VOTE_AGE)

    # Cast votes: 5 free_market, 4 social_democracy, 2 libertarian, 1 authoritarian
    for name in ["eco_gatherer1", "eco_gatherer2", "eco_trader", "eco_banker", "eco_miller"]:
        await agents[name].call("vote", {"government_type": "free_market"})
    for name in ["eco_baker", "eco_worker1", "eco_worker2", "eco_politician"]:
        await agents[name].call("vote", {"government_type": "social_democracy"})
    for name in ["eco_lumberjack", "eco_criminal"]:
        await agents[name].call("vote", {"government_type": "libertarian"})
    # eco_homeless tries to vote (may fail if deactivated)
    await agents["eco_homeless"].try_call("vote", {"government_type": "authoritarian"})
    print("  Votes cast: 5 free_market, 4 social_democracy, 2 libertarian")

    # Run weekly tick for election
    await run_tick(days=7)

    econ = await agents["eco_politician"].call("get_economy", {"section": "government"})
    current_gov = econ["current_template"]["slug"]
    assert current_gov == "free_market", f"Expected free_market to win, got {current_gov}"
    print(f"  Election winner: {current_gov}")

    # Tax rate
    tax_rate = econ["current_template"].get("tax_rate", 0)
    assert 0 < tax_rate <= 0.10, f"free_market tax should be ~5%, got {tax_rate}"
    print(f"  Tax rate: {tax_rate}")

    # ------------------------------------------------------------------
    # Tax collection
    # ------------------------------------------------------------------
    print_section("Tax collection")

    await give_balance(app, "eco_trader", 500)
    await give_inventory(app, "eco_gatherer1", "wheat", 20)
    await agents["eco_gatherer1"].call(
        "marketplace_order",
        {"action": "sell", "product": "wheat", "quantity": 10, "price": 3.0},
    )
    await agents["eco_trader"].call(
        "marketplace_order",
        {"action": "buy", "product": "wheat", "quantity": 10, "price": 5.0},
    )
    await run_tick(minutes=1)
    await run_tick(hours=1)

    async with app.state.session_factory() as session:
        tax_count = (
            await session.execute(select(func.count()).select_from(Transaction).where(Transaction.type == "tax"))
        ).scalar()
    print(f"  Tax transactions: {tax_count}")

    # ------------------------------------------------------------------
    # Jail mechanics (comprehensive)
    # ------------------------------------------------------------------
    print_section("Jail mechanics")

    await jail_agent(app, "eco_criminal", clock, hours=2.0)
    criminal = agents["eco_criminal"]
    cr_status = await criminal.status()
    assert cr_status.get("criminal_record", {}).get("jailed") is True
    print("  eco_criminal jailed for 2 hours")

    # Comprehensive blocked tools list (merged from simulation + adversarial)
    blocked = [
        ("gather", {"resource": "berries"}),
        ("work", {}),
        ("marketplace_order", {"action": "sell", "product": "berries", "quantity": 1, "price": 1.0}),
        ("register_business", {"name": "Jail Biz", "type": "mill", "zone": "industrial"}),
        (
            "trade",
            {
                "action": "propose",
                "target_agent": "eco_trader",
                "offer_items": [{"good_slug": "berries", "quantity": 1}],
                "request_items": [{"good_slug": "wood", "quantity": 1}],
            },
        ),
        ("apply_job", {"job_id": "00000000-0000-0000-0000-000000000000"}),
        ("set_prices", {"business_id": "00000000-0000-0000-0000-000000000000", "product": "bread", "price": 5}),
        ("configure_production", {"business_id": "00000000-0000-0000-0000-000000000000", "product": "bread"}),
    ]
    for tool_name, params in blocked:
        _, err = await criminal.try_call(tool_name, params)
        assert err == "IN_JAIL", f"Expected IN_JAIL for {tool_name}, got {err}"
    print(f"  {len(blocked)} tools blocked while jailed")

    # Allowed tools
    allowed = [
        ("get_status", {}),
        ("messages", {"action": "read"}),
        ("bank", {"action": "view_balance"}),
        ("marketplace_browse", {}),
        ("get_economy", {}),
    ]
    for tool_name, params in allowed:
        _, err = await criminal.try_call(tool_name, params)
        assert err is None or err != "IN_JAIL", f"{tool_name} should be allowed in jail, got {err}"
    print(f"  {len(allowed)} view-only tools allowed while jailed")

    # ------------------------------------------------------------------
    # Vote persistence across elections
    # ------------------------------------------------------------------
    print_section("Vote persistence across elections")

    async with app.state.session_factory() as session:
        await session.execute(_delete(Vote))
        await session.commit()

    voters = []
    for i in range(3):
        v = await TestAgent.signup(client, f"vote_persist_{i}")
        voters.append(v)
        await force_agent_age(app, f"vote_persist_{i}", 1_300_000)
        await v.call("vote", {"government_type": "libertarian"})

    # First election
    await redis_client.set("tick:last_weekly", str(clock.now().timestamp() - 700_000))
    clock.advance(100)
    await run_tick(seconds=1)

    async with app.state.session_factory() as session:
        gs = (await session.execute(select(GovernmentState).where(GovernmentState.id == 1))).scalar_one()
        assert gs.current_template_slug == "libertarian", f"Expected libertarian, got {gs.current_template_slug}"
    print("  First election: libertarian won")

    # Second election (7 days later): votes persist
    clock.advance(7 * 86400 + 3700)
    await redis_client.set("tick:last_weekly", str(clock.now().timestamp() - 700_000))
    await run_tick(seconds=1)

    async with app.state.session_factory() as session:
        gs2 = (await session.execute(select(GovernmentState).where(GovernmentState.id == 1))).scalar_one()
        assert gs2.current_template_slug == "libertarian", (
            f"Expected libertarian to persist, got {gs2.current_template_slug}"
        )
    print("  Second election: libertarian still winning (votes persisted)")

    # ------------------------------------------------------------------
    # Agent deactivation after 2 bankruptcies
    # ------------------------------------------------------------------
    print_section("Agent deactivation")

    deact = await TestAgent.signup(client, "deact_agent")

    # First bankruptcy
    await give_balance(app, "deact_agent", -250)
    clock.advance(3700)
    await run_tick(seconds=1)

    status = await deact.status()
    assert status["bankruptcy_count"] == 1
    assert status["is_active"] is True
    print("  After 1st bankruptcy: still active")

    # Second bankruptcy → deactivated
    await give_balance(app, "deact_agent", -250)
    clock.advance(3700)
    await run_tick(seconds=1)

    status = await deact.status()
    assert status["bankruptcy_count"] == 2
    assert status["is_active"] is False
    assert status["_hints"].get("deactivated") is True
    print("  After 2nd bankruptcy: deactivated")

    # Deactivated agent blocked from actions
    _, err = await deact.try_call("gather", {"resource": "berries"})
    assert err == "AGENT_DEACTIVATED"
    _, err = await deact.try_call("rent_housing", {"zone": "outskirts"})
    assert err == "AGENT_DEACTIVATED"
    _, err = await deact.try_call("bank", {"action": "deposit", "amount": 10})
    assert err == "AGENT_DEACTIVATED"
    print("  Deactivated agent blocked from gather, housing, bank")

    # Not charged survival costs
    bal_before = await get_balance(app, "deact_agent")
    clock.advance(3700)
    await run_tick(seconds=1)
    bal_after = await get_balance(app, "deact_agent")
    assert bal_after == bal_before, f"Deactivated agent was charged: {bal_before} → {bal_after}"
    print("  Deactivated agent not charged survival costs")

    # Run remaining time
    await run_tick(hours=120)

    print("\n  Finance & Law COMPLETE")
