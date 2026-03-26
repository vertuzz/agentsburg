"""Phase 1 of the government policy transitions test: free market baseline."""

from __future__ import annotations

from sqlalchemy import delete, select

from backend.models.government import GovernmentState, Vote
from tests.conftest import force_agent_age, give_balance
from tests.helpers import TestAgent
from tests.stress.helpers import assert_no_negative_inventory


async def phase1_free_market(client, app, clock, run_tick, redis_client) -> dict:
    """
    Establish free_market baseline with 6 voting-age agents.

    Returns shared state dict with agents, business IDs, and policy snapshots.
    """
    # Clean up votes from any other tests
    async with app.state.session_factory() as session:
        await session.execute(delete(Vote))
        await session.commit()
    print("  Cleaned up stale votes")

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
    mill_reg = await agents[0].call(
        "register_business",
        {
            "name": "Gov Mill",
            "type": "mill",
            "zone": "industrial",
        },
    )
    mill_id = mill_reg["business_id"]
    print("  Registered mill")

    bakery_reg = await agents[1].call(
        "register_business",
        {
            "name": "Gov Bakery",
            "type": "bakery",
            "zone": "suburbs",
        },
    )
    bakery_id = bakery_reg["business_id"]
    print("  Registered bakery")

    # Force free_market government
    gov_data = await agents[0].call("get_economy", {"section": "government"})
    async with app.state.session_factory() as session:
        gov_state = await session.execute(select(GovernmentState).where(GovernmentState.id == 1))
        gs = gov_state.scalar_one()
        gs.current_template_slug = "free_market"
        await session.commit()
    print("  Government set to free_market")

    # Re-fetch to confirm
    gov_data = await agents[0].call("get_economy", {"section": "government"})
    current = gov_data["current_template"]
    assert current["slug"] == "free_market", f"Expected free_market, got {current['slug']}"
    free_market_tax = current["tax_rate"]
    free_market_enforcement = current["enforcement_probability"]
    print(f"  Free market: tax={free_market_tax}, enforcement={free_market_enforcement}")
    assert free_market_tax == 0.05, f"Expected 5% tax, got {free_market_tax}"
    assert free_market_enforcement == 0.12, f"Expected 12% enforcement, got {free_market_enforcement}"

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

    await assert_no_negative_inventory(app, "Phase 1 End")

    return {
        "agents": agents,
        "mill_id": mill_id,
        "bakery_id": bakery_id,
        "free_market_tax": free_market_tax,
        "free_market_enforcement": free_market_enforcement,
        "free_market_balances": free_market_balances,
    }
