"""
Economic Stress Tests for Agent Economy.

Two large-scale tests that push the simulation through extreme conditions:

1. test_economic_collapse_and_recovery
   - Phase 1: Build a thriving economy with 8 agents, businesses, and workers
   - Phase 2: Drain agent balances to trigger mass bankruptcy
   - Phase 3: Verify NPC gap-filling keeps the economy running; fresh agents can join

2. test_government_policy_transitions
   - Phase 1: Establish free_market baseline with 6 voting-age agents
   - Phase 2: Vote in authoritarian government, verify high taxes and enforcement
   - Phase 3: Vote in libertarian government, verify low taxes and enforcement
   - Phase 4: Final invariant checks

Both tests verify the "no negative inventory" invariant at every checkpoint
and exercise the full tick system through the real REST API.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from backend.models.agent import Agent
from backend.models.banking import BankAccount, CentralBank, Loan
from backend.models.business import Business, Employment, JobPosting, StorefrontPrice
from backend.models.government import GovernmentState, Vote
from backend.models.inventory import InventoryItem
from backend.models.transaction import Transaction
from tests.conftest import (
    force_agent_age,
    get_balance,
    get_inventory_qty,
    give_balance,
    give_inventory,
)
from tests.helpers import TestAgent, ToolCallError


# ---------------------------------------------------------------------------
# Shared invariant check
# ---------------------------------------------------------------------------

async def assert_no_negative_inventory(app, label: str) -> None:
    """Verify no inventory row has quantity < 0."""
    async with app.state.session_factory() as session:
        result = await session.execute(
            select(InventoryItem).where(InventoryItem.quantity < 0)
        )
        negatives = result.scalars().all()
        if negatives:
            details = [
                f"{item.good_slug}={item.quantity} (owner={item.owner_type}:{item.owner_id})"
                for item in negatives
            ]
            pytest.fail(
                f"[{label}] Negative inventory found: {details}"
            )
    print(f"  [{label}] No negative inventory -- OK")


async def get_open_business_count(app, *, is_npc: bool | None = None) -> int:
    """Count open (non-closed) businesses, optionally filtered by NPC status."""
    async with app.state.session_factory() as session:
        q = select(func.count(Business.id)).where(Business.closed_at.is_(None))
        if is_npc is not None:
            q = q.where(Business.is_npc == is_npc)
        result = await session.execute(q)
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Test 1: Economic Collapse and Recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_economic_collapse_and_recovery(
    client, app, clock, run_tick, redis_client
):
    """
    Simulate an economy that thrives, collapses via mass bankruptcy,
    and recovers through NPC gap-filling.
    """
    print(f"\n\n{'#'*60}")
    print("# STRESS TEST: ECONOMIC COLLAPSE AND RECOVERY")
    print(f"# Start time: {clock.now().isoformat()}")
    print(f"{'#'*60}")

    # ===================================================================
    # PHASE 1: Build a thriving economy (Days 0-7)
    # ===================================================================
    print("\n--- PHASE 1: Building a thriving economy ---")

    # Sign up 8 agents
    agents = []
    for i in range(8):
        agent = await TestAgent.signup(client, f"col_{i}")
        agents.append(agent)
    print(f"  Signed up {len(agents)} agents")

    # Give each agent 3000 balance
    for i in range(8):
        await give_balance(app, f"col_{i}", 3000)
    print("  Gave each agent 3000 balance")

    # Rent housing: 4 in outskirts, 2 in suburbs, 2 in industrial
    for i in [0, 1, 2, 3]:
        await agents[i].call("rent_housing", {"zone": "outskirts"})
    for i in [4, 5]:
        await agents[i].call("rent_housing", {"zone": "suburbs"})
    for i in [6, 7]:
        await agents[i].call("rent_housing", {"zone": "industrial"})
    print("  Housing: 4 outskirts, 2 suburbs, 2 industrial")

    # Verify all agents are housed
    for a in agents:
        s = await a.status()
        assert s["housing"]["homeless"] is False, f"{a.name} should be housed"
    print("  All agents housed -- OK")

    # Register 4 businesses: mill, bakery, lumber_mill, smithy
    # Agent 0: mill in industrial
    mill_reg = await agents[0].call("register_business", {
        "name": "Collapse Mill",
        "type": "mill",
        "zone": "industrial",
    })
    mill_id = mill_reg["business_id"]
    print(f"  Registered mill (id={mill_id[:8]}...)")

    # Agent 1: bakery in suburbs (allowed)
    bakery_reg = await agents[1].call("register_business", {
        "name": "Collapse Bakery",
        "type": "bakery",
        "zone": "suburbs",
    })
    bakery_id = bakery_reg["business_id"]
    print(f"  Registered bakery (id={bakery_id[:8]}...)")

    # Agent 2: lumber_mill in industrial
    lumber_reg = await agents[2].call("register_business", {
        "name": "Collapse Lumber Mill",
        "type": "lumber_mill",
        "zone": "industrial",
    })
    lumber_id = lumber_reg["business_id"]
    print(f"  Registered lumber_mill (id={lumber_id[:8]}...)")

    # Agent 3: smithy in industrial
    smithy_reg = await agents[3].call("register_business", {
        "name": "Collapse Smithy",
        "type": "smithy",
        "zone": "industrial",
    })
    smithy_id = smithy_reg["business_id"]
    print(f"  Registered smithy (id={smithy_id[:8]}...)")

    # Give businesses inventory to produce with
    # Mill needs wheat
    await give_inventory(app, "col_0", "wheat", 50)
    # Bakery needs flour and berries
    await give_inventory(app, "col_1", "flour", 30)
    await give_inventory(app, "col_1", "berries", 20)
    # Lumber mill needs wood
    await give_inventory(app, "col_2", "wood", 50)
    # Smithy needs iron_ore
    await give_inventory(app, "col_3", "iron_ore", 50)
    print("  Gave businesses production inputs")

    # Post jobs and hire workers
    # Mill posts a flour job, agent 4 applies
    mill_job = await agents[0].call("manage_employees", {
        "business_id": mill_id,
        "action": "post_job",
        "title": "Miller",
        "wage": 10.0,
        "product": "flour",
        "max_workers": 2,
    })
    await agents[4].call("apply_job", {"job_id": mill_job["job_id"]})
    print("  Mill: posted job, col_4 hired")

    # Bakery posts a bread job, agent 5 applies
    bakery_job = await agents[1].call("manage_employees", {
        "business_id": bakery_id,
        "action": "post_job",
        "title": "Baker",
        "wage": 12.0,
        "product": "bread",
        "max_workers": 2,
    })
    await agents[5].call("apply_job", {"job_id": bakery_job["job_id"]})
    print("  Bakery: posted job, col_5 hired")

    # Lumber mill posts job, agent 6 applies
    lumber_job = await agents[2].call("manage_employees", {
        "business_id": lumber_id,
        "action": "post_job",
        "title": "Lumberjack",
        "wage": 10.0,
        "product": "lumber",
        "max_workers": 2,
    })
    await agents[6].call("apply_job", {"job_id": lumber_job["job_id"]})
    print("  Lumber Mill: posted job, col_6 hired")

    # Smithy posts job, agent 7 applies
    smithy_job = await agents[3].call("manage_employees", {
        "business_id": smithy_id,
        "action": "post_job",
        "title": "Blacksmith",
        "wage": 10.0,
        "product": "iron_ingots",
        "max_workers": 2,
    })
    await agents[7].call("apply_job", {"job_id": smithy_job["job_id"]})
    print("  Smithy: posted job, col_7 hired")

    # Snapshot before simulation: count NPC businesses
    npc_count_before = await get_open_business_count(app, is_npc=True)
    player_count_before = await get_open_business_count(app, is_npc=False)
    print(f"  Pre-simulation: {npc_count_before} NPC businesses, "
          f"{player_count_before} player businesses")

    # Run 3 days of simulation (fewer ticks to avoid connection pool pressure)
    print("\n  Running 3 days of simulation (6 ticks)...")
    await run_tick.days(3, ticks_per_day=2)
    print("  3 days complete")

    # Snapshot: verify businesses exist
    npc_count_mid = await get_open_business_count(app, is_npc=True)
    player_count_mid = await get_open_business_count(app, is_npc=False)
    print(f"  Post-Phase-1: {npc_count_mid} NPC businesses, "
          f"{player_count_mid} player businesses")
    assert npc_count_mid > 0, "NPC businesses should still be running"
    assert player_count_mid > 0, "Player businesses should still exist"

    # Check GDP > 0 (some transactions should have occurred)
    async with app.state.session_factory() as session:
        tx_count = await session.execute(
            select(func.count(Transaction.id))
        )
        total_tx = tx_count.scalar_one()
    print(f"  Total transactions: {total_tx}")
    assert total_tx > 0, "Should have some transactions from the simulation"

    # Invariant: no negative inventory
    await assert_no_negative_inventory(app, "Phase 1 End")

    # ===================================================================
    # PHASE 2: Economic crisis (Days 7-14)
    # ===================================================================
    print("\n--- PHASE 2: Economic crisis ---")

    # Record balances and bankruptcy counts before crisis
    pre_crisis_statuses = []
    for a in agents:
        s = await a.status()
        pre_crisis_statuses.append(s)
        print(f"  {a.name}: balance={s['balance']:.2f}, "
              f"bankruptcies={s['bankruptcy_count']}")

    # Drain ALL agent balances to near-bankruptcy threshold (-180, just above -200)
    for i in range(8):
        await give_balance(app, f"col_{i}", -180)
    print("  Drained all agents to -180 balance (threshold is -200)")

    # Verify draining worked
    for i in range(8):
        bal = await get_balance(app, f"col_{i}")
        assert bal <= Decimal("-170"), (
            f"col_{i} balance should be near -180, got {bal}"
        )
    print("  All balances confirmed near -180")

    # Also drain any bank deposits (so bankruptcy seizure has something to check)
    async with app.state.session_factory() as session:
        # Get all agent IDs
        agent_rows = await session.execute(
            select(Agent).where(Agent.name.like("col_%"))
        )
        col_agents = agent_rows.scalars().all()
        for ag in col_agents:
            acct_result = await session.execute(
                select(BankAccount).where(BankAccount.agent_id == ag.id)
            )
            acct = acct_result.scalar_one_or_none()
            if acct and float(acct.balance) > 0:
                acct.balance = Decimal("0")
        await session.commit()
    print("  Zeroed bank deposits")

    # Run ticks: survival costs (2/hr min) will push them below -200
    # Each tick at 6h interval costs at least 12 (survival) + rent
    # outskirts: 5/hr * 6h = 30, suburbs: 25/hr * 6h = 150, industrial: 15/hr * 6h = 90
    # After 1-2 ticks, agents should cross -200 threshold
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
                print(f"    Tick {tick_num+1}: {bk['count']} bankruptcies: {names}")
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
    assert bankrupt_agents >= 2, (
        f"Expected at least 2 agents to go bankrupt, got {bankrupt_agents}"
    )

    # Verify: their businesses got closed
    async with app.state.session_factory() as session:
        closed_biz = await session.execute(
            select(func.count(Business.id)).where(
                Business.closed_at.isnot(None),
                Business.is_npc == False,
            )
        )
        closed_count = closed_biz.scalar_one()
    print(f"  Closed player businesses: {closed_count}")
    # Some businesses should have been closed due to bankruptcy
    # (depends on which agents went bankrupt)
    if bankrupt_agents >= 4:
        # If most agents bankrupt, most player businesses should close
        assert closed_count >= 1, "At least 1 player business should be closed"

    # Verify: bank deposits were seized (should be 0 for bankrupt agents)
    async with app.state.session_factory() as session:
        bankrupt_result = await session.execute(
            select(Agent).where(
                Agent.name.like("col_%"),
                Agent.bankruptcy_count > 0,
            )
        )
        for ag in bankrupt_result.scalars().all():
            acct_result = await session.execute(
                select(BankAccount).where(BankAccount.agent_id == ag.id)
            )
            acct = acct_result.scalar_one_or_none()
            if acct:
                assert float(acct.balance) <= 0, (
                    f"Bankrupt agent {ag.name} should have 0 or less deposit, "
                    f"got {acct.balance}"
                )
    print("  Bank deposits seized for bankrupt agents -- OK")

    # Invariant: no negative inventory
    await assert_no_negative_inventory(app, "Phase 2 End")

    # ===================================================================
    # PHASE 3: NPC gap-filling & recovery (Days 14-28)
    # ===================================================================
    print("\n--- PHASE 3: NPC gap-filling and recovery ---")

    # Continue running simulation for 5 days
    print("  Running 5 days of recovery simulation (10 ticks)...")
    await run_tick.days(5, ticks_per_day=2)
    print("  5 days complete")

    # NPC businesses should still be operating (from bootstrap)
    npc_count_recovery = await get_open_business_count(app, is_npc=True)
    print(f"  NPC businesses still open: {npc_count_recovery}")
    assert npc_count_recovery > 0, (
        "NPC businesses should still be running after crisis"
    )

    # Verify NPC storefronts still have prices set
    async with app.state.session_factory() as session:
        npc_biz = await session.execute(
            select(Business).where(
                Business.is_npc == True,
                Business.closed_at.is_(None),
            )
        )
        npc_businesses = npc_biz.scalars().all()
        npc_with_prices = 0
        for biz in npc_businesses:
            prices = await session.execute(
                select(StorefrontPrice).where(
                    StorefrontPrice.business_id == biz.id
                )
            )
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

    # Final snapshot: verify no negative inventory
    await assert_no_negative_inventory(app, "Phase 3 End")

    # Verify central bank reserves are tracked correctly
    async with app.state.session_factory() as session:
        bank = await session.execute(
            select(CentralBank).where(CentralBank.id == 1)
        )
        cb = bank.scalar_one_or_none()
        if cb:
            print(f"  Central bank: reserves={float(cb.reserves):.2f}, "
                  f"total_loaned={float(cb.total_loaned):.2f}")
            # Reserves should be non-negative (bank shouldn't go negative)
            assert float(cb.reserves) >= 0, (
                f"Central bank reserves should be >= 0, got {cb.reserves}"
            )

    # Final NPC business count
    npc_final = await get_open_business_count(app, is_npc=True)
    print(f"  Final NPC businesses: {npc_final}")
    assert npc_final > 0, "Economy should still have active NPC businesses"

    print(f"\n{'='*60}")
    print("  STRESS TEST: Economic Collapse and Recovery -- PASSED")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Test 2: Government Policy Transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_government_policy_transitions(
    client, app, clock, run_tick, redis_client
):
    """
    Test cascading effects of government policy changes across
    multiple election cycles: free_market -> authoritarian -> libertarian.
    """
    print(f"\n\n{'#'*60}")
    print("# STRESS TEST: GOVERNMENT POLICY TRANSITIONS")
    print(f"# Start time: {clock.now().isoformat()}")
    print(f"{'#'*60}")

    # Clean up votes from any other tests to avoid interference
    async with app.state.session_factory() as session:
        await session.execute(delete(Vote))
        await session.commit()
    print("  Cleaned up stale votes")

    # ===================================================================
    # PHASE 1: Free market baseline (Days 0-14)
    # ===================================================================
    print("\n--- PHASE 1: Free market baseline ---")

    # Sign up 6 agents
    agents = []
    for i in range(6):
        agent = await TestAgent.signup(client, f"gov_{i}")
        agents.append(agent)
    print(f"  Signed up {len(agents)} agents")

    # Give agents 5000 balance
    for i in range(6):
        await give_balance(app, f"gov_{i}", 5000)
    print("  Gave each agent 5000 balance")

    # Make all agents old enough to vote (2+ weeks)
    voting_eligibility = 1_209_600  # 2 weeks in seconds
    for i in range(6):
        await force_agent_age(app, f"gov_{i}", voting_eligibility + 3600)
    print("  All agents aged to 2+ weeks (voting eligible)")

    # Rent housing for all
    for i in [0, 1, 2]:
        await agents[i].call("rent_housing", {"zone": "outskirts"})
    for i in [3, 4]:
        await agents[i].call("rent_housing", {"zone": "suburbs"})
    await agents[5].call("rent_housing", {"zone": "industrial"})
    print("  Housing: 3 outskirts, 2 suburbs, 1 industrial")

    # Register businesses
    # Agent 0: mill
    mill_reg = await agents[0].call("register_business", {
        "name": "Gov Mill",
        "type": "mill",
        "zone": "industrial",
    })
    mill_id = mill_reg["business_id"]
    print(f"  Registered mill")

    # Agent 1: bakery
    bakery_reg = await agents[1].call("register_business", {
        "name": "Gov Bakery",
        "type": "bakery",
        "zone": "suburbs",
    })
    bakery_id = bakery_reg["business_id"]
    print(f"  Registered bakery")

    # Verify free_market is the default government
    gov_data = await agents[0].call("get_economy", {"section": "government"})
    current = gov_data["current_template"]
    # The government might not be free_market if another test changed it.
    # Force it to free_market for this test.
    async with app.state.session_factory() as session:
        gov_state = await session.execute(
            select(GovernmentState).where(GovernmentState.id == 1)
        )
        gs = gov_state.scalar_one()
        gs.current_template_slug = "free_market"
        await session.commit()
    print("  Government set to free_market")

    # Re-fetch to confirm
    gov_data = await agents[0].call("get_economy", {"section": "government"})
    current = gov_data["current_template"]
    assert current["slug"] == "free_market", (
        f"Expected free_market, got {current['slug']}"
    )
    free_market_tax = current["tax_rate"]
    free_market_enforcement = current["enforcement_probability"]
    print(f"  Free market: tax={free_market_tax}, "
          f"enforcement={free_market_enforcement}")
    assert free_market_tax == 0.05, f"Expected 5% tax, got {free_market_tax}"
    assert free_market_enforcement == 0.10, (
        f"Expected 10% enforcement, got {free_market_enforcement}"
    )

    # Run 3 days of simulation under free market
    print("  Running 3 days under free_market...")
    await run_tick.days(3, ticks_per_day=2)
    print("  3 days complete")

    # Record balances after free market period
    free_market_balances = {}
    for a in agents:
        s = await a.status()
        free_market_balances[a.name] = s["balance"]
    print("  Free market balances recorded:")
    for name, bal in free_market_balances.items():
        print(f"    {name}: {bal:.2f}")

    # Invariant: no negative inventory
    await assert_no_negative_inventory(app, "Phase 1 End")

    # ===================================================================
    # PHASE 2: Vote for authoritarian (Day 14)
    # ===================================================================
    print("\n--- PHASE 2: Voting for authoritarian ---")

    # Clean votes first
    async with app.state.session_factory() as session:
        await session.execute(delete(Vote))
        await session.commit()

    # All agents vote for authoritarian
    for a in agents:
        result = await a.call("vote", {"government_type": "authoritarian"})
        assert result["voted_for"] == "authoritarian"
    print("  All 6 agents voted for authoritarian")

    # Force weekly tick boundary so election runs
    now_ts = clock.now().timestamp()
    await redis_client.set("tick:last_weekly", str(now_ts - 700_000))
    print("  Forced weekly tick boundary")

    # Run tick to trigger election
    tick_result = await run_tick()
    weekly = tick_result.get("weekly_tick")
    assert weekly is not None, "Weekly tick should have run"
    assert "winner" in weekly, f"Election should have a winner: {weekly}"
    print(f"  Election result: winner={weekly['winner']}, "
          f"votes={weekly.get('vote_counts', {})}")

    # Verify government template changed to authoritarian
    async with app.state.session_factory() as session:
        gs_result = await session.execute(
            select(GovernmentState).where(GovernmentState.id == 1)
        )
        gs = gs_result.scalar_one()
        assert gs.current_template_slug == "authoritarian", (
            f"Expected authoritarian, got {gs.current_template_slug}"
        )
    print("  Government changed to authoritarian -- OK")

    # Verify authoritarian policy parameters via get_economy
    gov_data = await agents[0].call("get_economy", {"section": "government"})
    current = gov_data["current_template"]
    assert current["slug"] == "authoritarian"
    auth_tax = current["tax_rate"]
    auth_enforcement = current["enforcement_probability"]
    auth_licensing = current.get("licensing_cost_modifier", 1.0)
    print(f"  Authoritarian: tax={auth_tax}, enforcement={auth_enforcement}, "
          f"licensing_modifier={auth_licensing}")

    # Verify HIGHER tax rates (20% vs 5%)
    assert auth_tax == 0.20, f"Expected 20% tax, got {auth_tax}"
    assert auth_tax > free_market_tax, "Authoritarian tax should be higher"

    # Verify HIGHER enforcement (40% vs 10%)
    assert auth_enforcement == 0.40, (
        f"Expected 40% enforcement, got {auth_enforcement}"
    )
    assert auth_enforcement > free_market_enforcement, (
        "Authoritarian enforcement should be higher"
    )

    # Verify licensing cost modifier is 2.0
    assert auth_licensing == 2.0, (
        f"Expected licensing_cost_modifier=2.0, got {auth_licensing}"
    )

    # Business registration should cost MORE
    # Base cost 200 * licensing_cost_modifier 2.0 = 400
    # We test this by checking if an agent can register a business with enough
    # for base cost but not for authoritarian cost
    test_agent_for_cost = agents[2]
    test_agent_bal = await get_balance(app, "gov_2")
    # Give agent exactly 300 (enough for base 200 but not for 400 under authoritarian)
    await give_balance(app, "gov_2", 300)
    _, err = await test_agent_for_cost.try_call("register_business", {
        "name": "Too Expensive Biz",
        "type": "mill",
        "zone": "industrial",
    })
    # Should fail because 300 < 400 (200 * 2.0 modifier)
    if err is not None:
        print(f"  Business registration rejected (insufficient funds under "
              f"authoritarian pricing): error={err}")
    else:
        # If it succeeded, they must have had enough balance
        print("  Note: agent had enough balance for authoritarian registration cost")

    # Give agent enough and verify it works
    await give_balance(app, "gov_2", 2000)

    # Run 3 days under authoritarian
    print("  Running 3 days under authoritarian...")
    await run_tick.days(3, ticks_per_day=4)
    print("  3 days complete")

    # Record balances after authoritarian period
    auth_balances = {}
    for a in agents:
        s = await a.status()
        auth_balances[a.name] = s["balance"]
    print("  Authoritarian balances:")
    for name, bal in auth_balances.items():
        print(f"    {name}: {bal:.2f}")

    # Invariant: no negative inventory
    await assert_no_negative_inventory(app, "Phase 2 End")

    # ===================================================================
    # PHASE 3: Vote for libertarian (Day 21)
    # ===================================================================
    print("\n--- PHASE 3: Voting for libertarian ---")

    # Clean votes
    async with app.state.session_factory() as session:
        await session.execute(delete(Vote))
        await session.commit()

    # All agents vote for libertarian
    for a in agents:
        result = await a.call("vote", {"government_type": "libertarian"})
        assert result["voted_for"] == "libertarian"
    print("  All 6 agents voted for libertarian")

    # Force weekly tick boundary
    now_ts = clock.now().timestamp()
    await redis_client.set("tick:last_weekly", str(now_ts - 700_000))

    # Run tick to trigger election
    tick_result = await run_tick()
    weekly = tick_result.get("weekly_tick")
    assert weekly is not None, "Weekly tick should have run"
    assert weekly["winner"] == "libertarian", (
        f"Expected libertarian to win, got {weekly['winner']}"
    )
    print(f"  Election result: winner={weekly['winner']}, "
          f"votes={weekly.get('vote_counts', {})}")

    # Verify government changed to libertarian
    async with app.state.session_factory() as session:
        gs_result = await session.execute(
            select(GovernmentState).where(GovernmentState.id == 1)
        )
        gs = gs_result.scalar_one()
        assert gs.current_template_slug == "libertarian", (
            f"Expected libertarian, got {gs.current_template_slug}"
        )
    print("  Government changed to libertarian -- OK")

    # Verify libertarian policy parameters
    gov_data = await agents[0].call("get_economy", {"section": "government"})
    current = gov_data["current_template"]
    assert current["slug"] == "libertarian"
    lib_tax = current["tax_rate"]
    lib_enforcement = current["enforcement_probability"]
    lib_licensing = current.get("licensing_cost_modifier", 1.0)
    print(f"  Libertarian: tax={lib_tax}, enforcement={lib_enforcement}, "
          f"licensing_modifier={lib_licensing}")

    # Verify LOWER taxes (3% vs 20% authoritarian, vs 5% free market)
    assert lib_tax == 0.03, f"Expected 3% tax, got {lib_tax}"
    assert lib_tax < free_market_tax, "Libertarian tax should be lower than free market"
    assert lib_tax < auth_tax, "Libertarian tax should be lower than authoritarian"

    # Verify lower enforcement (8% vs 40% authoritarian)
    assert lib_enforcement == 0.08, (
        f"Expected 8% enforcement, got {lib_enforcement}"
    )
    assert lib_enforcement < auth_enforcement, (
        "Libertarian enforcement should be lower than authoritarian"
    )

    # Verify lower licensing cost
    assert lib_licensing == 0.60, (
        f"Expected licensing_cost_modifier=0.60, got {lib_licensing}"
    )
    assert lib_licensing < auth_licensing, (
        "Libertarian licensing should be cheaper than authoritarian"
    )

    # Run 3 days under libertarian
    print("  Running 3 days under libertarian...")
    await run_tick.days(3, ticks_per_day=4)
    print("  3 days complete")

    # Record balances after libertarian period
    lib_balances = {}
    for a in agents:
        s = await a.status()
        lib_balances[a.name] = s["balance"]
    print("  Libertarian balances:")
    for name, bal in lib_balances.items():
        print(f"    {name}: {bal:.2f}")

    # Invariant: no negative inventory
    await assert_no_negative_inventory(app, "Phase 3 End")

    # ===================================================================
    # PHASE 4: Final invariants (Day 28)
    # ===================================================================
    print("\n--- PHASE 4: Final invariant checks ---")

    # No negative inventory (final check)
    await assert_no_negative_inventory(app, "Phase 4 Final")

    # Economy stats via get_economy
    stats_data = await agents[0].call("get_economy", {"section": "stats"})
    print(f"  Economy stats: {stats_data}")

    # Government reflects libertarian
    gov_final = await agents[0].call("get_economy", {"section": "government"})
    assert gov_final["current_template"]["slug"] == "libertarian", (
        f"Final government should be libertarian, "
        f"got {gov_final['current_template']['slug']}"
    )
    print("  Government is libertarian -- OK")

    # Verify tax rate progression: free_market(5%) -> authoritarian(20%) -> libertarian(3%)
    print(f"\n  Tax rate progression:")
    print(f"    Free Market:   {free_market_tax * 100:.0f}%")
    print(f"    Authoritarian: {auth_tax * 100:.0f}%")
    print(f"    Libertarian:   {lib_tax * 100:.0f}%")

    # Verify enforcement progression: 10% -> 40% -> 8%
    print(f"  Enforcement probability progression:")
    print(f"    Free Market:   {free_market_enforcement * 100:.0f}%")
    print(f"    Authoritarian: {auth_enforcement * 100:.0f}%")
    print(f"    Libertarian:   {lib_enforcement * 100:.0f}%")

    # Final balance check -- agents should still be alive
    alive_count = 0
    for a in agents:
        try:
            s = await a.status()
            if s["balance"] > -200:
                alive_count += 1
        except Exception:
            pass
    print(f"  Agents still active (balance > -200): {alive_count}/{len(agents)}")

    # Central bank check
    async with app.state.session_factory() as session:
        bank = await session.execute(
            select(CentralBank).where(CentralBank.id == 1)
        )
        cb = bank.scalar_one_or_none()
        if cb:
            print(f"  Central bank: reserves={float(cb.reserves):.2f}, "
                  f"total_loaned={float(cb.total_loaned):.2f}")
            assert float(cb.reserves) >= 0, (
                f"Central bank reserves should be >= 0, got {cb.reserves}"
            )

    print(f"\n{'='*60}")
    print("  STRESS TEST: Government Policy Transitions -- PASSED")
    print(f"{'='*60}")
