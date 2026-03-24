"""Phase 7: Government & Law (Days 14-21) — voting, elections, taxes, audits, jail."""

from __future__ import annotations

from sqlalchemy import delete as _delete
from sqlalchemy import func, select

from backend.models.government import Vote
from backend.models.transaction import Transaction
from tests.conftest import force_agent_age, give_balance, give_inventory, jail_agent
from tests.helpers import TestAgent
from tests.simulation.helpers import AGENT_NAMES, print_phase, print_section


async def run_phase_7(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Test voting, elections, tax collection, and jail mechanics."""
    print_phase(7, "GOVERNMENT & LAW")

    # --- 7a: Make agents old enough to vote (2 weeks) ---
    print_section("Aging agents for voting eligibility")

    # Clean up any votes from prior tests in the session (DB is session-scoped)
    async with app.state.session_factory() as session:
        await session.execute(_delete(Vote))
        await session.commit()

    VOTE_AGE = 1209600 + 100  # 2 weeks + buffer
    for name in AGENT_NAMES:
        await force_agent_age(app, name, VOTE_AGE)
    print(f"  All agents aged to {VOTE_AGE}s")

    # --- 7b: Cast votes ---
    print_section("Casting votes")

    free_market_voters = ["eco_gatherer1", "eco_gatherer2", "eco_trader", "eco_banker", "eco_miller"]
    social_dem_voters = ["eco_baker", "eco_worker1", "eco_worker2", "eco_politician"]
    libertarian_voters = ["eco_lumberjack", "eco_criminal"]
    authoritarian_voters = ["eco_homeless"]

    for name in free_market_voters:
        result = await agents[name].call("vote", {"government_type": "free_market"})
        assert "template" in result or "voted_for" in result or "message" in result
    print(f"  {len(free_market_voters)} agents voted free_market")

    for name in social_dem_voters:
        result = await agents[name].call("vote", {"government_type": "social_democracy"})
    print(f"  {len(social_dem_voters)} agents voted social_democracy")

    for name in libertarian_voters:
        await agents[name].call("vote", {"government_type": "libertarian"})
    print(f"  {len(libertarian_voters)} agents voted libertarian")

    authoritarian_voted = 0
    for name in authoritarian_voters:
        result, err = await agents[name].try_call("vote", {"government_type": "authoritarian"})
        if err is None:
            authoritarian_voted += 1
        else:
            print(f"  {name} could not vote: {err}")
    print(f"  {authoritarian_voted}/{len(authoritarian_voters)} agents voted authoritarian")

    # --- 7c: Run weekly tick for election ---
    print_section("Running weekly tick (election)")

    tick_result = await run_tick(days=7)
    weekly = tick_result.get("weekly_tick") or {}
    election = weekly.get("election") or {}
    if election:
        print(f"  Election result: {election}")
    else:
        print("  Weekly tick ran (election may be in tick result)")

    # Check current government
    econ = await agents["eco_politician"].call("get_economy", {"section": "government"})
    current_gov = econ["current_template"]["slug"]
    print(f"  Current government: {current_gov}")
    assert current_gov == "free_market", f"Expected free_market to win election, got {current_gov}"
    print("  Election: free_market won with most votes")

    # --- 7d: Verify policy effects ---
    tax_rate = econ["current_template"].get("tax_rate", 0)
    print(f"  Tax rate: {tax_rate}")
    assert 0 < tax_rate <= 0.10, f"free_market tax should be ~5%, got {tax_rate}"

    # --- 7e: Run ticks for tax collection ---
    print_section("Tax collection on marketplace income")

    await give_balance(app, "eco_trader", 500)
    await give_inventory(app, "eco_gatherer1", "wheat", 20)

    await agents["eco_gatherer1"].call(
        "marketplace_order",
        {
            "action": "sell",
            "product": "wheat",
            "quantity": 10,
            "price": 3.0,
        },
    )
    await agents["eco_trader"].call(
        "marketplace_order",
        {
            "action": "buy",
            "product": "wheat",
            "quantity": 10,
            "price": 5.0,
        },
    )
    await run_tick(minutes=1)

    await run_tick(hours=1)

    async with app.state.session_factory() as session:
        tax_count = (
            await session.execute(select(func.count()).select_from(Transaction).where(Transaction.type == "tax"))
        ).scalar()
    print(f"  Tax transactions recorded: {tax_count}")

    # --- 7f: Jail test ---
    print_section("Jail mechanics")

    await jail_agent(app, "eco_criminal", clock, hours=2.0)
    criminal = agents["eco_criminal"]

    criminal_status = await criminal.status()
    cr = criminal_status.get("criminal_record", {})
    assert cr.get("jailed") is True, f"Expected jailed=True, got: {cr}"
    print("  eco_criminal jailed for 2 hours")

    # Jailed agent BLOCKED from these actions:
    blocked_actions = [
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
    ]

    for tool_name, params in blocked_actions:
        _, err = await criminal.try_call(tool_name, params)
        assert err is not None, f"Jailed agent should be blocked from {tool_name}"
        print(f"    Blocked: {tool_name} (error={err})")

    # Jailed agent CAN do these:
    allowed_actions = [
        ("get_status", {}),
        ("messages", {"action": "read"}),
        ("bank", {"action": "view_balance"}),
        ("marketplace_browse", {}),
    ]

    for tool_name, params in allowed_actions:
        result, err = await criminal.try_call(tool_name, params)
        assert err is None, f"Jailed agent should be allowed {tool_name}, got error={err}"
        print(f"    Allowed: {tool_name}")

    # Run more days
    await run_tick(hours=120)

    print("\n  Phase 7 COMPLETE")
