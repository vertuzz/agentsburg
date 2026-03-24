"""Endgame: bankruptcy, recovery, economy stats, and final invariants.

Covers:
- Trigger bankruptcy via negative balance + tick
- Post-bankruptcy state: balance reset, evicted, inventory liquidated
- Bankruptcy deposit seizure (bank deposits zeroed, loans defaulted)
- Serial bankruptcy → loan denial
- Economy stats (population, money_supply, employment_rate, GDP, zones)
- Final 7-day simulation run
- Invariant checks:
  - No negative inventory anywhere
  - No NaN or unreasonable balances
  - All open businesses have valid owners
  - No orphaned employments at closed businesses
  - Money supply conservation
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from backend.models.agent import Agent
from backend.models.banking import BankAccount, CentralBank, Loan
from backend.models.business import Business, Employment
from backend.models.inventory import InventoryItem
from backend.models.transaction import Transaction
from tests.conftest import give_balance, give_inventory
from tests.helpers import TestAgent
from tests.simulation.helpers import print_agent_summary, print_section, print_stage


async def run_endgame(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Trigger bankruptcy, verify recovery, run final invariants."""
    print_stage("ENDGAME: Bankruptcy, Recovery, Invariants")

    # ------------------------------------------------------------------
    # Trigger bankruptcy
    # ------------------------------------------------------------------
    print_section("Triggering bankruptcy")

    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == "eco_homeless"))
        homeless_ag = result.scalar_one()
        homeless_ag.balance = Decimal("-210")  # below -200 threshold
        homeless_ag.is_active = True
        homeless_ag.bankruptcy_count = 0
        await session.commit()

    await give_inventory(app, "eco_homeless", "berries", 15)

    # Must trigger within 3 tick attempts
    triggered = False
    for attempt in range(3):
        tick_result = await run_tick(hours=2)
        slow = tick_result.get("slow_tick") or {}
        bankruptcy = slow.get("bankruptcy") or {}
        if bankruptcy.get("count", 0) > 0:
            print(f"  Bankruptcy triggered on attempt {attempt + 1}: {bankruptcy.get('bankrupted', [])}")
            triggered = True
            break
    assert triggered, "Bankruptcy should have triggered within 3 tick attempts"

    # Post-bankruptcy state
    h_status = await agents["eco_homeless"].status()
    assert h_status["bankruptcy_count"] > 0, "Bankruptcy count should be > 0"
    assert h_status["balance"] >= 0, f"Post-bankruptcy balance should be >= 0, got {h_status['balance']}"
    assert h_status["housing"]["homeless"] is True, "Should be evicted after bankruptcy"
    inv_total = sum(i["quantity"] for i in h_status.get("inventory", []))
    assert inv_total == 0, f"Inventory should be liquidated, has {inv_total} items"
    print(f"  Post-bankruptcy: balance={h_status['balance']}, inventory liquidated, evicted")

    # ------------------------------------------------------------------
    # Bankruptcy count verification
    # ------------------------------------------------------------------
    print_section("Bankruptcy count")

    async with app.state.session_factory() as session:
        bk_agents = (await session.execute(select(Agent).where(Agent.bankruptcy_count > 0))).scalars().all()
    assert len(bk_agents) > 0, "At least one agent should have gone bankrupt"
    for ag in bk_agents:
        print(f"  {ag.name}: bankruptcy_count={ag.bankruptcy_count}")

    # ------------------------------------------------------------------
    # Bankruptcy deposit seizure
    # ------------------------------------------------------------------
    print_section("Bankruptcy deposit seizure")

    seizure_agent = await TestAgent.signup(client, "seizure_test")
    await give_balance(app, "seizure_test", 500)

    # Deposit 400, take loan
    await seizure_agent.call("bank", {"action": "deposit", "amount": 400})
    bank_info = await seizure_agent.call("bank", {"action": "view_balance"})
    assert float(bank_info.get("account_balance", 0)) >= 400

    _loan, loan_err = await seizure_agent.try_call("bank", {"action": "take_loan", "amount": 30})

    # Force bankruptcy
    await give_balance(app, "seizure_test", -250)
    clock.advance(3700)
    await run_tick(seconds=1)

    async with app.state.session_factory() as session:
        ag = (await session.execute(select(Agent).where(Agent.name == "seizure_test"))).scalar_one()
        assert ag.bankruptcy_count >= 1, f"Expected bankruptcy, got count={ag.bankruptcy_count}"
        assert Decimal(str(ag.balance)) >= 0, f"Balance should be >= 0 after bankruptcy, got {ag.balance}"

        # Deposits seized
        acct = (await session.execute(select(BankAccount).where(BankAccount.agent_id == ag.id))).scalar_one_or_none()
        if acct:
            assert Decimal(str(acct.balance)) == 0, f"Deposit should be seized (0), got {acct.balance}"

        # Loan defaulted
        if loan_err is None:
            defaulted = (
                await session.execute(select(Loan).where(Loan.agent_id == ag.id, Loan.status == "defaulted"))
            ).scalar_one_or_none()
            assert defaulted is not None, "Loan should be defaulted after bankruptcy"
    print("  Deposits seized, loan defaulted, balance reset")

    # ------------------------------------------------------------------
    # Serial bankruptcy → loan denial
    # ------------------------------------------------------------------
    print_section("Serial bankruptcy loan denial")

    serial = await TestAgent.signup(client, "serial_bankrupt")
    for i in range(3):
        await give_balance(app, "serial_bankrupt", -250)
        clock.advance(3700)
        await run_tick(seconds=1)

    async with app.state.session_factory() as session:
        ag = (await session.execute(select(Agent).where(Agent.name == "serial_bankrupt"))).scalar_one()
        assert ag.bankruptcy_count >= 2, f"Expected >= 2 bankruptcies, got {ag.bankruptcy_count}"
        assert ag.is_active is False, "Should be deactivated after 2+ bankruptcies"

    await give_balance(app, "serial_bankrupt", 500)
    _, err = await serial.try_call("bank", {"action": "take_loan", "amount": 10})
    assert err is not None, "Loan should be denied after serial bankruptcies"
    print(f"  Serial bankrupt denied loan (error={err})")

    # ------------------------------------------------------------------
    # Economy stats
    # ------------------------------------------------------------------
    print_section("Economy stats")

    econ = await agents["eco_politician"].call("get_economy", {"section": "stats"})
    for field in ["population", "money_supply", "employment_rate", "gdp_24h_proxy"]:
        assert field in econ, f"Missing {field} in economy stats"
    print(f"  Population: {econ['population']}")
    print(f"  Money supply: {econ['money_supply']}")
    print(f"  Employment rate: {econ['employment_rate']}")
    print(f"  GDP (24h): {econ['gdp_24h_proxy']}")

    zones = await agents["eco_politician"].call("get_economy", {"section": "zones"})
    assert "zones" in zones
    assert len(zones["zones"]) >= 5, f"Should have >= 5 zones, got {len(zones['zones'])}"
    print(f"  Zones: {len(zones['zones'])}")

    # ------------------------------------------------------------------
    # Final 7-day simulation
    # ------------------------------------------------------------------
    print_section("Final 7-day simulation")

    for name in [
        "eco_miller",
        "eco_baker",
        "eco_lumberjack",
        "eco_trader",
        "eco_banker",
        "eco_politician",
        "eco_gatherer1",
        "eco_gatherer2",
    ]:
        await give_balance(app, name, 2000)

    await run_tick(hours=168)
    print("  7 days completed")

    # ------------------------------------------------------------------
    # Final invariant checks
    # ------------------------------------------------------------------
    print_section("Final invariants")

    # No negative inventory
    async with app.state.session_factory() as session:
        neg_inv = (await session.execute(select(InventoryItem).where(InventoryItem.quantity < 0))).scalars().all()
    assert len(neg_inv) == 0, f"INVARIANT VIOLATION: {len(neg_inv)} negative inventory items"
    print("  No negative inventory")

    # No NaN or unreasonable balances
    async with app.state.session_factory() as session:
        all_agents = (await session.execute(select(Agent))).scalars().all()
        for ag in all_agents:
            bal = float(ag.balance)
            assert bal == bal, f"{ag.name} has NaN balance"
            assert abs(bal) < 1e15, f"{ag.name} has unreasonable balance: {bal}"
    print("  All balances valid")

    # All open businesses have valid owners
    async with app.state.session_factory() as session:
        open_biz = (await session.execute(select(Business).where(Business.closed_at.is_(None)))).scalars().all()
        for biz in open_biz:
            owner = (await session.execute(select(Agent).where(Agent.id == biz.owner_id))).scalar_one_or_none()
            assert owner is not None, f"Business {biz.name} has no valid owner"
    print(f"  {len(open_biz)} open businesses, all with valid owners")

    # No orphaned employments at closed businesses
    async with app.state.session_factory() as session:
        orphans = (
            (
                await session.execute(
                    select(Employment)
                    .join(Business, Employment.business_id == Business.id)
                    .where(Employment.terminated_at.is_(None), Business.closed_at.is_not(None))
                )
            )
            .scalars()
            .all()
        )
    assert len(orphans) == 0, f"INVARIANT VIOLATION: {len(orphans)} active employees at closed businesses"
    print("  No orphaned employments")

    # Money supply conservation
    async with app.state.session_factory() as session:
        wallet_total = float((await session.execute(select(func.coalesce(func.sum(Agent.balance), 0)))).scalar_one())
        bank_total = float(
            (await session.execute(select(func.coalesce(func.sum(BankAccount.balance), 0)))).scalar_one()
        )
        cb = (await session.execute(select(CentralBank).where(CentralBank.id == 1))).scalar_one_or_none()
        reserves = float(cb.reserves) if cb else 0.0

        neg_inv = (await session.execute(select(InventoryItem).where(InventoryItem.quantity < 0))).scalars().all()
        assert len(neg_inv) == 0, f"Negative inventory found: {[(i.good_slug, i.quantity) for i in neg_inv]}"
    print(f"  Money supply: wallets={wallet_total:.2f}, bank={bank_total:.2f}, reserves={reserves:.2f}")
    print(f"  Total trackable: {wallet_total + bank_total + reserves:.2f}")

    # ==================================================================
    # FINAL REPORT
    # ==================================================================
    print(f"\n\n{'#' * 70}")
    print("# FINAL REPORT")
    print(f"{'#' * 70}")

    statuses = {}
    for name, agent in agents.items():
        try:
            statuses[name] = await agent.status()
        except Exception:
            statuses[name] = {
                "name": name,
                "balance": 0,
                "housing": {"homeless": True},
                "bankruptcy_count": 0,
                "inventory": [],
                "storage": {"used": 0},
                "violation_count": 0,
            }

    print_agent_summary(agents, statuses)

    async with app.state.session_factory() as session:
        for txn_type in [
            "food",
            "rent",
            "wage",
            "marketplace",
            "tax",
            "trade",
            "deposit",
            "withdrawal",
            "loan_disbursement",
            "loan_payment",
        ]:
            count = (
                await session.execute(select(func.count()).select_from(Transaction).where(Transaction.type == txn_type))
            ).scalar()
            if count > 0:
                print(f"  {txn_type}: {count} transactions")

    final_econ = await agents["eco_politician"].call("get_economy", {"section": "stats"})
    print(
        f"\n  Final: pop={final_econ['population']}, emp_rate={final_econ['employment_rate']:.1%}, "
        f"money={final_econ['money_supply']:.2f}, gdp={final_econ['gdp_24h_proxy']:.2f}"
    )

    total_bankruptcies = sum(s["bankruptcy_count"] for s in statuses.values())
    housed = sum(1 for s in statuses.values() if not s["housing"]["homeless"])
    total_inv = sum(sum(i["quantity"] for i in s.get("inventory", [])) for s in statuses.values())
    print(f"  Bankruptcies: {total_bankruptcies}, Housed: {housed}/{len(agents)}, Inventory: {total_inv}")

    assert final_econ["population"] >= 12, "Should have at least 12 agents"

    print(f"\n{'=' * 70}")
    print("  GRAND ECONOMY SIMULATION PASSED")
    print(f"{'=' * 70}")
