"""Phases 2-3 of the economic collapse test: crisis and recovery."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from backend.models.agent import Agent
from backend.models.banking import BankAccount, CentralBank
from backend.models.business import Business, StorefrontPrice
from backend.models.inventory import InventoryItem
from tests.conftest import get_balance, give_balance
from tests.helpers import TestAgent
from tests.stress.helpers import assert_no_negative_inventory, get_open_business_count


async def phase2_economic_crisis(app, clock, run_tick, state: dict) -> dict:
    """
    Drain agent balances to trigger mass bankruptcy.

    Returns updated state with bankruptcy counts.
    """
    print("\n--- PHASE 2: Economic crisis ---")
    agents = state["agents"]

    # Record balances and bankruptcy counts before crisis
    pre_crisis_statuses = []
    for a in agents:
        s = await a.status()
        pre_crisis_statuses.append(s)
        print(f"  {a.name}: balance={s['balance']:.2f}, bankruptcies={s['bankruptcy_count']}")

    # Drain ALL agent balances to near-bankruptcy threshold
    for i in range(8):
        await give_balance(app, f"col_{i}", -180)
    print("  Drained all agents to -180 balance (threshold is -200)")

    # Verify draining worked
    for i in range(8):
        bal = await get_balance(app, f"col_{i}")
        assert bal <= Decimal("-170"), f"col_{i} balance should be near -180, got {bal}"
    print("  All balances confirmed near -180")

    # Also drain any bank deposits
    async with app.state.session_factory() as session:
        agent_rows = await session.execute(select(Agent).where(Agent.name.like("col_%")))
        col_agents = agent_rows.scalars().all()
        for ag in col_agents:
            acct_result = await session.execute(select(BankAccount).where(BankAccount.agent_id == ag.id))
            acct = acct_result.scalar_one_or_none()
            if acct and float(acct.balance) > 0:
                acct.balance = Decimal("0")
        await session.commit()
    print("  Zeroed bank deposits")

    # Run ticks to push agents below -200 threshold
    print("  Running 4 days of crisis ticks (8 ticks)...")
    bankruptcy_count = 0
    for tick_num in range(8):
        result = await run_tick(hours=12)
        slow = result.get("slow_tick")
        if slow:
            bk = slow.get("bankruptcy", {})
            if bk.get("count", 0) > 0:
                bankruptcy_count += bk["count"]
                names = bk.get("bankrupted", [])
                print(f"    Tick {tick_num + 1}: {bk['count']} bankruptcies: {names}")
    print(f"  Crisis phase complete: {bankruptcy_count} total bankruptcies")

    # Verify: multiple agents went bankrupt
    bankrupt_agents = 0
    async with app.state.session_factory() as session:
        result = await session.execute(
            select(Agent).where(
                Agent.name.like("col_%"),
                Agent.bankruptcy_count > 0,
            )
        )
        bankrupt_list = result.scalars().all()
        bankrupt_agents = len(bankrupt_list)
        for ag in bankrupt_list:
            print(f"  {ag.name}: bankruptcy_count={ag.bankruptcy_count}")
    print(f"  Total agents who went bankrupt: {bankrupt_agents}")
    assert bankrupt_agents >= 2, f"Expected at least 2 agents to go bankrupt, got {bankrupt_agents}"

    # Verify: their businesses got closed
    async with app.state.session_factory() as session:
        closed_biz = await session.execute(
            select(func.count(Business.id)).where(
                Business.closed_at.isnot(None),
                Business.is_npc == False,  # noqa: E712
            )
        )
        closed_count = closed_biz.scalar_one()
    print(f"  Closed player businesses: {closed_count}")
    if bankrupt_agents >= 4:
        assert closed_count >= 1, "At least 1 player business should be closed"

    # Verify: bank deposits were seized
    async with app.state.session_factory() as session:
        bankrupt_result = await session.execute(
            select(Agent).where(
                Agent.name.like("col_%"),
                Agent.bankruptcy_count > 0,
            )
        )
        for ag in bankrupt_result.scalars().all():
            acct_result = await session.execute(select(BankAccount).where(BankAccount.agent_id == ag.id))
            acct = acct_result.scalar_one_or_none()
            if acct:
                assert float(acct.balance) <= 0, (
                    f"Bankrupt agent {ag.name} should have 0 or less deposit, got {acct.balance}"
                )
    print("  Bank deposits seized for bankrupt agents -- OK")

    await assert_no_negative_inventory(app, "Phase 2 End")

    state["bankrupt_agents"] = bankrupt_agents
    return state


async def phase3_recovery(client, app, clock, run_tick, state: dict) -> None:
    """
    Verify NPC gap-filling keeps the economy running and fresh agents can join.
    """
    print("\n--- PHASE 3: NPC gap-filling and recovery ---")

    # Continue running simulation for 5 days
    print("  Running 5 days of recovery simulation (10 ticks)...")
    await run_tick.days(5, ticks_per_day=2)
    print("  5 days complete")

    # NPC businesses should still be operating
    npc_count_recovery = await get_open_business_count(app, is_npc=True)
    print(f"  NPC businesses still open: {npc_count_recovery}")
    assert npc_count_recovery > 0, "NPC businesses should still be running after crisis"

    # Verify NPC storefronts still have prices set
    async with app.state.session_factory() as session:
        npc_biz = await session.execute(
            select(Business).where(
                Business.is_npc,
                Business.closed_at.is_(None),
            )
        )
        npc_businesses = npc_biz.scalars().all()
        npc_with_prices = 0
        for biz in npc_businesses:
            prices = await session.execute(select(StorefrontPrice).where(StorefrontPrice.business_id == biz.id))
            if prices.scalars().all():
                npc_with_prices += 1
    print(f"  NPC businesses with storefront prices: {npc_with_prices}/{len(npc_businesses)}")
    assert npc_with_prices > 0, "At least some NPC businesses should have prices"

    # Create 2 fresh agents to verify new agents can still participate
    fresh_agents = []
    for i in range(2):
        fresh = await TestAgent.signup(client, f"col_fresh_{i}")
        fresh_agents.append(fresh)
    print("  Signed up 2 fresh agents")

    # Give fresh agents balance
    for i in range(2):
        await give_balance(app, f"col_fresh_{i}", 500)

    # Fresh agents can gather
    for fresh in fresh_agents:
        result = await fresh.call("gather", {"resource": "berries"})
        assert result["gathered"] == "berries"
        assert result["quantity"] == 1
    print("  Fresh agents can gather -- OK")

    # Fresh agents can rent housing
    clock.advance(3)  # skip global gather cooldown
    for fresh in fresh_agents:
        result = await fresh.call("rent_housing", {"zone": "outskirts"})
        assert result["zone_slug"] == "outskirts"
    print("  Fresh agents can rent housing -- OK")

    # Verify fresh agents are housed and functioning
    for fresh in fresh_agents:
        s = await fresh.status()
        assert s["housing"]["homeless"] is False
        assert s["balance"] > 0
    print("  Fresh agents are housed with positive balance -- OK")

    await assert_no_negative_inventory(app, "Phase 3 End")

    # Verify central bank reserves
    async with app.state.session_factory() as session:
        bank = await session.execute(select(CentralBank).where(CentralBank.id == 1))
        cb = bank.scalar_one_or_none()
        if cb:
            print(f"  Central bank: reserves={float(cb.reserves):.2f}, total_loaned={float(cb.total_loaned):.2f}")
            assert float(cb.reserves) >= 0, f"Central bank reserves should be >= 0, got {cb.reserves}"

    # Verify NPC inventory drain prevents stuck storage
    async with app.state.session_factory() as session:
        npc_biz_result = await session.execute(
            select(Business).where(
                Business.is_npc == True,  # noqa: E712
                Business.closed_at.is_(None),
            )
        )
        for biz in npc_biz_result.scalars().all():
            inv_result = await session.execute(
                select(func.coalesce(func.sum(InventoryItem.quantity), 0)).where(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == biz.id,
                )
            )
            total_inv = int(inv_result.scalar_one())
            # After drain, NPC inventory should stay well below full capacity.
            # Use 90% as threshold (drain targets 50%, but production adds
            # goods after drain within the same tick cycle).
            cap = biz.storage_capacity
            assert total_inv <= int(cap * 0.9), (
                f"NPC {biz.name} inventory {total_inv} near full capacity {cap} — drain not working"
            )
    print("  NPC business inventories below full capacity — drain working")

    # Final NPC business count
    npc_final = await get_open_business_count(app, is_npc=True)
    print(f"  Final NPC businesses: {npc_final}")
    assert npc_final > 0, "Economy should still have active NPC businesses"
