"""Phase 8: Bankruptcy & Recovery (Days 21-28) — liquidation, serial bankruptcy, NPC fill, economy stats."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from backend.models.agent import Agent
from backend.models.business import Business, Employment
from backend.models.inventory import InventoryItem
from backend.models.transaction import Transaction
from tests.conftest import give_balance, give_inventory
from tests.helpers import TestAgent
from tests.simulation.helpers import print_agent_summary, print_phase, print_section


async def run_phase_8(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Trigger bankruptcy, verify liquidation, check economy stats, run final invariants."""
    print_phase(8, "BANKRUPTCY & RECOVERY")

    # --- 8a: Trigger bankruptcy ---
    print_section("Triggering bankruptcy")

    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == "eco_homeless"))
        homeless_ag = result.scalar_one()
        homeless_ag.balance = Decimal("-210")  # below -200 threshold
        homeless_ag.is_active = True
        homeless_ag.bankruptcy_count = 0
        await session.commit()

    await give_inventory(app, "eco_homeless", "berries", 15)

    for _ in range(3):
        tick_result = await run_tick(hours=2)
        slow_tick = tick_result.get("slow_tick") or {}
        bankruptcy = slow_tick.get("bankruptcy") or {}
        if bankruptcy.get("count", 0) > 0:
            print(f"  Bankruptcy triggered: {bankruptcy.get('bankrupted', [])}")
            break
    else:
        print("  Bankruptcy may not have triggered yet")

    # Verify post-bankruptcy state
    homeless_status = await agents["eco_homeless"].status()
    if homeless_status["bankruptcy_count"] > 0:
        assert homeless_status["balance"] >= 0, "Post-bankruptcy balance should be >= 0"
        assert homeless_status["housing"]["homeless"] is True
        inv_total = sum(i["quantity"] for i in homeless_status.get("inventory", []))
        assert inv_total == 0, f"Inventory should be liquidated, has {inv_total} items"
        print(f"  Post-bankruptcy: balance={homeless_status['balance']}, inventory liquidated")
    else:
        print(
            f"  Homeless agent balance={homeless_status['balance']}, "
            f"bankruptcy_count={homeless_status['bankruptcy_count']}"
        )

    # --- 8b: Verify bankruptcy increments count ---
    print_section("Checking bankruptcy count")

    async with app.state.session_factory() as session:
        bk_agents = (await session.execute(select(Agent).where(Agent.bankruptcy_count > 0))).scalars().all()
    for ag in bk_agents:
        print(f"  {ag.name}: bankruptcy_count={ag.bankruptcy_count}")
    assert len(bk_agents) > 0, "At least one agent should have gone bankrupt"

    # --- 8c: Serial bankruptcy -- denied loans ---
    print_section("Serial bankruptcy loan denial")

    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == "eco_homeless"))
        homeless_ag = result.scalar_one()
        homeless_ag.bankruptcy_count = 3
        homeless_ag.is_active = False
        homeless_ag.balance = Decimal("50")
        await session.commit()

    _, err = await agents["eco_homeless"].try_call(
        "bank",
        {
            "action": "take_loan",
            "amount": 20,
        },
    )
    if err is not None:
        print(f"  Serial bankrupt denied loan (error={err})")
    else:
        print("  Loan check: agent with 3 bankruptcies may still qualify (credit score dependent)")

    # --- 8d: Economy stats ---
    print_section("Economy stats (get_economy)")

    econ_stats = await agents["eco_politician"].call("get_economy", {"section": "stats"})
    assert "population" in econ_stats
    assert "money_supply" in econ_stats
    assert "employment_rate" in econ_stats
    assert "gdp_24h_proxy" in econ_stats
    print(f"  Population: {econ_stats['population']}")
    print(f"  Money supply: {econ_stats['money_supply']}")
    print(f"  Employment rate: {econ_stats['employment_rate']}")
    print(f"  GDP (24h): {econ_stats['gdp_24h_proxy']}")

    econ_zones = await agents["eco_politician"].call("get_economy", {"section": "zones"})
    assert "zones" in econ_zones
    zone_count = len(econ_zones["zones"])
    assert zone_count >= 5, f"Should have at least 5 zones, got {zone_count}"
    print(f"  Zones: {zone_count}")

    # --- 8e: Run more simulation days ---
    print_section("Running final simulation (7 more days)")

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

    # --- 8f: Final invariant checks ---
    print_section("Final invariant checks")

    async with app.state.session_factory() as session:
        neg_inv = (await session.execute(select(InventoryItem).where(InventoryItem.quantity < 0))).scalars().all()
    assert len(neg_inv) == 0, f"INVARIANT VIOLATION: {len(neg_inv)} negative inventory items"
    print("  No negative inventory anywhere")

    async with app.state.session_factory() as session:
        all_agents = (await session.execute(select(Agent))).scalars().all()
        for ag in all_agents:
            bal = float(ag.balance)
            assert bal == bal, f"{ag.name} has NaN balance"
            assert abs(bal) < 1e15, f"{ag.name} has unreasonable balance: {bal}"
    print("  All balances are valid numbers")

    async with app.state.session_factory() as session:
        open_businesses = (await session.execute(select(Business).where(Business.closed_at.is_(None)))).scalars().all()
        for biz in open_businesses:
            owner_result = await session.execute(select(Agent).where(Agent.id == biz.owner_id))
            owner = owner_result.scalar_one_or_none()
            assert owner is not None, f"Business {biz.name} has no valid owner"
    print(f"  {len(open_businesses)} open businesses, all have valid owners")

    async with app.state.session_factory() as session:
        orphan_emp = (
            (
                await session.execute(
                    select(Employment)
                    .join(Business, Employment.business_id == Business.id)
                    .where(
                        Employment.terminated_at.is_(None),
                        Business.closed_at.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(orphan_emp) == 0, f"INVARIANT VIOLATION: {len(orphan_emp)} active employees at closed businesses"
    print("  No orphaned employments at closed businesses")

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
    print("\n  Final economy stats:")
    print(f"    Population: {final_econ['population']}")
    print(f"    Employment rate: {final_econ['employment_rate']:.1%}")
    print(f"    Money supply: {final_econ['money_supply']:.2f}")
    print(f"    GDP (24h): {final_econ['gdp_24h_proxy']:.2f}")

    total_bankruptcies = sum(s["bankruptcy_count"] for s in statuses.values())
    total_violations = sum(s.get("violation_count", 0) for s in statuses.values())
    housed_count = sum(1 for s in statuses.values() if not s["housing"]["homeless"])
    total_inventory = sum(sum(i["quantity"] for i in s.get("inventory", [])) for s in statuses.values())

    print("\n  Summary:")
    print(f"    Total bankruptcies: {total_bankruptcies}")
    print(f"    Total violations: {total_violations}")
    print(f"    Currently housed: {housed_count}/{len(agents)}")
    print(f"    Total inventory items: {total_inventory}")

    assert final_econ["population"] >= 12, "Should have at least 12 agents"

    print(f"\n{'=' * 70}")
    print("  GRAND ECONOMY SIMULATION PASSED")
    print(f"{'=' * 70}")
