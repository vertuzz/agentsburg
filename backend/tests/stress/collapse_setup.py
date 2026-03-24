"""Phase 1 of the economic collapse test: build a thriving economy."""

from __future__ import annotations

from sqlalchemy import func, select

from backend.models.transaction import Transaction
from tests.conftest import give_balance, give_inventory
from tests.helpers import TestAgent
from tests.stress.helpers import assert_no_negative_inventory, get_open_business_count


async def phase1_build_economy(client, app, clock, run_tick) -> dict:
    """
    Build a thriving economy with 8 agents, 4 businesses, and workers.

    Returns shared state dict with agents, business IDs, and snapshot counts.
    """
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
    mill_reg = await agents[0].call(
        "register_business",
        {
            "name": "Collapse Mill",
            "type": "mill",
            "zone": "industrial",
        },
    )
    mill_id = mill_reg["business_id"]
    print(f"  Registered mill (id={mill_id[:8]}...)")

    bakery_reg = await agents[1].call(
        "register_business",
        {
            "name": "Collapse Bakery",
            "type": "bakery",
            "zone": "suburbs",
        },
    )
    bakery_id = bakery_reg["business_id"]
    print(f"  Registered bakery (id={bakery_id[:8]}...)")

    lumber_reg = await agents[2].call(
        "register_business",
        {
            "name": "Collapse Lumber Mill",
            "type": "lumber_mill",
            "zone": "industrial",
        },
    )
    lumber_id = lumber_reg["business_id"]
    print(f"  Registered lumber_mill (id={lumber_id[:8]}...)")

    smithy_reg = await agents[3].call(
        "register_business",
        {
            "name": "Collapse Smithy",
            "type": "smithy",
            "zone": "industrial",
        },
    )
    smithy_id = smithy_reg["business_id"]
    print(f"  Registered smithy (id={smithy_id[:8]}...)")

    # Give businesses inventory to produce with
    await give_inventory(app, "col_0", "wheat", 50)
    await give_inventory(app, "col_1", "flour", 30)
    await give_inventory(app, "col_1", "berries", 20)
    await give_inventory(app, "col_2", "wood", 50)
    await give_inventory(app, "col_3", "iron_ore", 50)
    print("  Gave businesses production inputs")

    # Post jobs and hire workers
    mill_job = await agents[0].call(
        "manage_employees",
        {
            "business_id": mill_id,
            "action": "post_job",
            "title": "Miller",
            "wage": 10.0,
            "product": "flour",
            "max_workers": 2,
        },
    )
    await agents[4].call("apply_job", {"job_id": mill_job["job_id"]})
    print("  Mill: posted job, col_4 hired")

    bakery_job = await agents[1].call(
        "manage_employees",
        {
            "business_id": bakery_id,
            "action": "post_job",
            "title": "Baker",
            "wage": 12.0,
            "product": "bread",
            "max_workers": 2,
        },
    )
    await agents[5].call("apply_job", {"job_id": bakery_job["job_id"]})
    print("  Bakery: posted job, col_5 hired")

    lumber_job = await agents[2].call(
        "manage_employees",
        {
            "business_id": lumber_id,
            "action": "post_job",
            "title": "Lumberjack",
            "wage": 10.0,
            "product": "lumber",
            "max_workers": 2,
        },
    )
    await agents[6].call("apply_job", {"job_id": lumber_job["job_id"]})
    print("  Lumber Mill: posted job, col_6 hired")

    smithy_job = await agents[3].call(
        "manage_employees",
        {
            "business_id": smithy_id,
            "action": "post_job",
            "title": "Blacksmith",
            "wage": 10.0,
            "product": "iron_ingots",
            "max_workers": 2,
        },
    )
    await agents[7].call("apply_job", {"job_id": smithy_job["job_id"]})
    print("  Smithy: posted job, col_7 hired")

    # Snapshot before simulation
    npc_count_before = await get_open_business_count(app, is_npc=True)
    player_count_before = await get_open_business_count(app, is_npc=False)
    print(f"  Pre-simulation: {npc_count_before} NPC businesses, {player_count_before} player businesses")

    # Run 3 days of simulation
    print("\n  Running 3 days of simulation (6 ticks)...")
    await run_tick.days(3, ticks_per_day=2)
    print("  3 days complete")

    # Snapshot: verify businesses exist
    npc_count_mid = await get_open_business_count(app, is_npc=True)
    player_count_mid = await get_open_business_count(app, is_npc=False)
    print(f"  Post-Phase-1: {npc_count_mid} NPC businesses, {player_count_mid} player businesses")
    assert npc_count_mid > 0, "NPC businesses should still be running"
    assert player_count_mid > 0, "Player businesses should still exist"

    # Check GDP > 0
    async with app.state.session_factory() as session:
        tx_count = await session.execute(select(func.count(Transaction.id)))
        total_tx = tx_count.scalar_one()
    print(f"  Total transactions: {total_tx}")
    assert total_tx > 0, "Should have some transactions from the simulation"

    await assert_no_negative_inventory(app, "Phase 1 End")

    return {
        "agents": agents,
        "mill_id": mill_id,
        "bakery_id": bakery_id,
        "lumber_id": lumber_id,
        "smithy_id": smithy_id,
    }
