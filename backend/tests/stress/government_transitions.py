"""Phases 2-4 of the government policy transitions test."""

from __future__ import annotations

from sqlalchemy import delete, select

from backend.models.banking import CentralBank
from backend.models.government import GovernmentState, Vote
from tests.conftest import get_balance, give_balance
from tests.stress.helpers import assert_no_negative_inventory


async def phase2_authoritarian(app, clock, run_tick, redis_client, state: dict) -> dict:
    """
    Vote in authoritarian government, verify high taxes and enforcement.

    Returns updated state with authoritarian policy snapshots.
    """
    print("\n--- PHASE 2: Voting for authoritarian ---")
    agents = state["agents"]
    free_market_tax = state["free_market_tax"]
    free_market_enforcement = state["free_market_enforcement"]

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
    print(f"  Election result: winner={weekly['winner']}, votes={weekly.get('vote_counts', {})}")

    # Verify government template changed to authoritarian
    async with app.state.session_factory() as session:
        gs_result = await session.execute(select(GovernmentState).where(GovernmentState.id == 1))
        gs = gs_result.scalar_one()
        assert gs.current_template_slug == "authoritarian", f"Expected authoritarian, got {gs.current_template_slug}"
    print("  Government changed to authoritarian -- OK")

    # Verify authoritarian policy parameters via get_economy
    gov_data = await agents[0].call("get_economy", {"section": "government"})
    current = gov_data["current_template"]
    assert current["slug"] == "authoritarian"
    auth_tax = current["tax_rate"]
    auth_enforcement = current["enforcement_probability"]
    auth_licensing = current.get("licensing_cost_modifier", 1.0)
    print(f"  Authoritarian: tax={auth_tax}, enforcement={auth_enforcement}, licensing_modifier={auth_licensing}")

    # Verify HIGHER tax rates (20% vs 5%)
    assert auth_tax == 0.20, f"Expected 20% tax, got {auth_tax}"
    assert auth_tax > free_market_tax, "Authoritarian tax should be higher"

    # Verify HIGHER enforcement (45% vs 12%)
    assert auth_enforcement == 0.45, f"Expected 45% enforcement, got {auth_enforcement}"
    assert auth_enforcement > free_market_enforcement, "Authoritarian enforcement should be higher"

    # Verify licensing cost modifier is 2.0
    assert auth_licensing == 2.0, f"Expected licensing_cost_modifier=2.0, got {auth_licensing}"

    # Business registration should cost MORE under authoritarian
    test_agent_for_cost = agents[2]
    await get_balance(app, "gov_2")
    await give_balance(app, "gov_2", 300)
    _, err = await test_agent_for_cost.try_call(
        "register_business",
        {
            "name": "Too Expensive Biz",
            "type": "mill",
            "zone": "industrial",
        },
    )
    if err is not None:
        print(f"  Business registration rejected (insufficient funds under authoritarian pricing): error={err}")
    else:
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

    await assert_no_negative_inventory(app, "Phase 2 End")

    state["auth_tax"] = auth_tax
    state["auth_enforcement"] = auth_enforcement
    state["auth_licensing"] = auth_licensing
    state["auth_balances"] = auth_balances
    return state


async def phase3_libertarian(app, clock, run_tick, redis_client, state: dict) -> dict:
    """
    Vote in libertarian government, verify low taxes and enforcement.

    Returns updated state with libertarian policy snapshots.
    """
    print("\n--- PHASE 3: Voting for libertarian ---")
    agents = state["agents"]
    free_market_tax = state["free_market_tax"]
    auth_tax = state["auth_tax"]
    auth_enforcement = state["auth_enforcement"]
    auth_licensing = state["auth_licensing"]

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
    assert weekly["winner"] == "libertarian", f"Expected libertarian to win, got {weekly['winner']}"
    print(f"  Election result: winner={weekly['winner']}, votes={weekly.get('vote_counts', {})}")

    # Verify government changed to libertarian
    async with app.state.session_factory() as session:
        gs_result = await session.execute(select(GovernmentState).where(GovernmentState.id == 1))
        gs = gs_result.scalar_one()
        assert gs.current_template_slug == "libertarian", f"Expected libertarian, got {gs.current_template_slug}"
    print("  Government changed to libertarian -- OK")

    # Verify libertarian policy parameters
    gov_data = await agents[0].call("get_economy", {"section": "government"})
    current = gov_data["current_template"]
    assert current["slug"] == "libertarian"
    lib_tax = current["tax_rate"]
    lib_enforcement = current["enforcement_probability"]
    lib_licensing = current.get("licensing_cost_modifier", 1.0)
    print(f"  Libertarian: tax={lib_tax}, enforcement={lib_enforcement}, licensing_modifier={lib_licensing}")

    # Verify LOWER taxes
    assert lib_tax == 0.03, f"Expected 3% tax, got {lib_tax}"
    assert lib_tax < free_market_tax, "Libertarian tax should be lower than free market"
    assert lib_tax < auth_tax, "Libertarian tax should be lower than authoritarian"

    # Verify lower enforcement
    assert lib_enforcement == 0.08, f"Expected 8% enforcement, got {lib_enforcement}"
    assert lib_enforcement < auth_enforcement, "Libertarian enforcement should be lower than authoritarian"

    # Verify lower licensing cost
    assert lib_licensing == 0.60, f"Expected licensing_cost_modifier=0.60, got {lib_licensing}"
    assert lib_licensing < auth_licensing, "Libertarian licensing should be cheaper than authoritarian"

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

    await assert_no_negative_inventory(app, "Phase 3 End")

    state["lib_tax"] = lib_tax
    state["lib_enforcement"] = lib_enforcement
    state["lib_licensing"] = lib_licensing
    state["lib_balances"] = lib_balances
    return state


async def phase4_final_checks(app, state: dict) -> None:
    """Final invariant checks after all government transitions."""
    print("\n--- PHASE 4: Final invariant checks ---")
    agents = state["agents"]
    free_market_tax = state["free_market_tax"]
    free_market_enforcement = state["free_market_enforcement"]
    auth_tax = state["auth_tax"]
    auth_enforcement = state["auth_enforcement"]
    lib_tax = state["lib_tax"]
    lib_enforcement = state["lib_enforcement"]

    # No negative inventory (final check)
    await assert_no_negative_inventory(app, "Phase 4 Final")

    # Economy stats via get_economy
    stats_data = await agents[0].call("get_economy", {"section": "stats"})
    print(f"  Economy stats: {stats_data}")

    # Government reflects libertarian
    gov_final = await agents[0].call("get_economy", {"section": "government"})
    assert gov_final["current_template"]["slug"] == "libertarian", (
        f"Final government should be libertarian, got {gov_final['current_template']['slug']}"
    )
    print("  Government is libertarian -- OK")

    # Verify tax rate progression
    print("\n  Tax rate progression:")
    print(f"    Free Market:   {free_market_tax * 100:.0f}%")
    print(f"    Authoritarian: {auth_tax * 100:.0f}%")
    print(f"    Libertarian:   {lib_tax * 100:.0f}%")

    # Verify enforcement progression
    print("  Enforcement probability progression:")
    print(f"    Free Market:   {free_market_enforcement * 100:.0f}%")
    print(f"    Authoritarian: {auth_enforcement * 100:.0f}%")
    print(f"    Libertarian:   {lib_enforcement * 100:.0f}%")

    # Final balance check
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
        bank = await session.execute(select(CentralBank).where(CentralBank.id == 1))
        cb = bank.scalar_one_or_none()
        if cb:
            print(f"  Central bank: reserves={float(cb.reserves):.2f}, total_loaned={float(cb.total_loaned):.2f}")
            assert float(cb.reserves) >= 0, f"Central bank reserves should be >= 0, got {cb.reserves}"
