"""Bootstrap: signup, gathering, housing, and survival costs.

Covers:
- Input validation & XSS prevention on signup
- Agent signup (12 agents), initial status verification
- Gathering mechanics, cooldowns, storage info
- Status response quality (hints, events, cooldowns)
- Leaderboard and API discovery
- Housing in multiple zones
- 2-day survival simulation with rent and food costs
- Transaction and event verification
"""

from __future__ import annotations

from sqlalchemy import func, select

from backend.models.transaction import Transaction
from tests.conftest import get_balance, give_balance
from tests.helpers import TestAgent
from tests.simulation.helpers import AGENT_NAMES, print_section, print_stage


async def _try_signup(client, name, model="test-model"):
    """Attempt signup, returning (result, None) or (None, error_code)."""
    response = await client.post("/v1/signup", json={"name": name, "model": model})
    body = response.json()
    if response.status_code == 400:
        return None, body.get("error_code", "UNKNOWN")
    if response.status_code != 200:
        return None, "UNKNOWN"
    return body.get("data"), None


async def run_bootstrap(client, app, clock, run_tick, redis_client):
    """Sign up agents, test gathering/cooldowns/XSS, rent housing, verify survival costs.

    Returns the agents dict.
    """
    print_stage("BOOTSTRAP: Signup, Gathering, Housing, Survival")

    # ------------------------------------------------------------------
    # Input validation & XSS prevention
    # ------------------------------------------------------------------
    print_section("Input validation & XSS prevention")

    xss_cases = [
        ("<script>alert('xss')</script>", "XSS script tag"),
        ("bob<evil", "angle bracket"),
        ("alice&bob", "ampersand"),
        ("", "empty string"),
        ("a", "single char (below minLength=2)"),
    ]
    for bad_name, description in xss_cases:
        _, err = await _try_signup(client, bad_name)
        assert err == "INVALID_PARAMS", f"Expected INVALID_PARAMS for {description} ({bad_name!r}), got {err}"
    print(f"  Rejected {len(xss_cases)} invalid signup attempts")

    # ------------------------------------------------------------------
    # Sign up 12 agents
    # ------------------------------------------------------------------
    print_section("Signing up 12 agents")

    agents: dict[str, TestAgent] = {}
    for name in AGENT_NAMES:
        agent = await TestAgent.signup(client, name)
        agents[name] = agent
    assert len(agents) == 12
    print(f"  Signed up: {', '.join(AGENT_NAMES)}")

    # ------------------------------------------------------------------
    # Verify initial status
    # ------------------------------------------------------------------
    print_section("Verifying initial status")

    for name, agent in agents.items():
        s = await agent.status()
        assert s["balance"] == 15.0, f"{name} balance should be 15, got {s['balance']}"
        assert s["housing"]["homeless"] is True, f"{name} should be homeless"
        assert s["bankruptcy_count"] == 0, f"{name} should have 0 bankruptcies"
    print("  All agents: balance=15, homeless, 0 bankruptcies")

    # ------------------------------------------------------------------
    # Gathering mechanics
    # ------------------------------------------------------------------
    print_section("Gathering mechanics")

    g1 = agents["eco_gatherer1"]

    # Gather berries: verify cash_on_gather vs base_value distinction
    result = await g1.call("gather", {"resource": "berries"})
    assert result["gathered"] == "berries"
    assert result["quantity"] == 1
    assert result["cooldown_seconds"] == 25
    assert result["cash_earned"] == 1.0, f"berries cash_on_gather should be 1, got {result['cash_earned']}"
    assert result["base_value"] == 2, f"berries base_value should be 2, got {result['base_value']}"
    # Storage info in gather response
    assert "storage" in result, "Gather response should include storage info"
    assert result["storage"]["capacity"] == 100
    assert result["storage"]["used"] >= 1
    assert result["storage"]["free"] <= 99
    print(f"  Gathered berries: cash=1, base=2, storage={result['storage']['used']}/{result['storage']['capacity']}")

    # Cooldown: immediate retry on same resource blocked
    _, err = await g1.try_call("gather", {"resource": "berries"})
    assert err == "COOLDOWN_ACTIVE", f"Expected COOLDOWN_ACTIVE on immediate retry, got {err}"
    print("  Same-resource cooldown enforced")

    # Different resource after global cooldown (5s)
    clock.advance(6)
    result2 = await g1.call("gather", {"resource": "wood"})
    assert result2["gathered"] == "wood"
    print("  Different resource OK after global cooldown")

    # Copper ore: verify distinct cash_on_gather vs base_value
    clock.advance(6)
    copper = await g1.call("gather", {"resource": "copper_ore"})
    assert copper["cash_earned"] == 4.0, f"copper cash_on_gather should be 4, got {copper['cash_earned']}"
    assert copper["base_value"] == 6, f"copper base_value should be 6, got {copper['base_value']}"
    print("  Copper ore: cash=4, base=6 verified")

    # Gather wheat and herbs for later use
    clock.advance(6)
    await g1.call("gather", {"resource": "wheat"})
    clock.advance(6)
    await g1.call("gather", {"resource": "herbs"})

    # Non-gatherable resource rejected
    clock.advance(6)
    _, err = await g1.try_call("gather", {"resource": "bread"})
    assert err is not None, "bread should not be gatherable"
    print(f"  Non-gatherable resource rejected (error={err})")

    # ------------------------------------------------------------------
    # Status response quality
    # ------------------------------------------------------------------
    print_section("Status response quality")

    g1_status = await g1.status()

    # Onboarding hints: homeless agents should get housing, employment, loan suggestions
    hints = g1_status.get("_hints", {})
    assert "next_steps" in hints, "Status should include _hints.next_steps"
    next_steps = hints["next_steps"]
    assert len(next_steps) > 0, "next_steps should not be empty"
    assert any("housing" in t.lower() for t in next_steps), "Homeless agent should get housing hint"
    assert any("job" in t.lower() or "employment" in t.lower() for t in next_steps), (
        "New agent should get employment hint"
    )
    assert any("loan" in t.lower() for t in next_steps), "Low-balance agent should get starter loan hint"

    # Ordering: housing before employment
    housing_idx = next(i for i, t in enumerate(next_steps) if "housing" in t.lower())
    job_idx = next(i for i, t in enumerate(next_steps) if "job" in t.lower() or "employment" in t.lower())
    assert housing_idx < job_idx, "Housing tip should appear before employment tip"
    print("  Onboarding hints: housing, employment, loan — in correct order")

    # Economy events count
    assert "economy_events" in g1_status, "Status should include economy_events"
    assert isinstance(g1_status["economy_events"], int)
    print(f"  economy_events={g1_status['economy_events']}")

    # Cooldown format
    cooldowns = g1_status.get("cooldowns", {})
    if "gather:berries" in cooldowns:
        cd = cooldowns["gather:berries"]
        assert "remaining" in cd, "Cooldown should have 'remaining'"
        assert "total" in cd, "Cooldown should have 'total'"
        assert cd["total"] == 25, f"berries cooldown total should be 25, got {cd['total']}"
        print(f"  Cooldown format: remaining={cd['remaining']}, total={cd['total']}")

    # ------------------------------------------------------------------
    # Leaderboard
    # ------------------------------------------------------------------
    print_section("Leaderboard")

    lb = await g1.call("leaderboard", {})
    assert "leaderboard" in lb
    for entry in lb["leaderboard"]:
        assert "is_npc" in entry, f"Leaderboard entry missing is_npc: {entry}"
        assert isinstance(entry["is_npc"], bool)
    print(f"  {len(lb['leaderboard'])} entries, all with is_npc flag")

    # ------------------------------------------------------------------
    # API discovery
    # ------------------------------------------------------------------
    print_section("API discovery")

    tools_resp = await client.get("/v1/tools")
    assert tools_resp.status_code == 200
    endpoints = tools_resp.json()["data"]["endpoints"]
    assert len(endpoints) >= 20, f"Expected >= 20 endpoints, got {len(endpoints)}"
    print(f"  /v1/tools: {len(endpoints)} endpoints")

    rules_resp = await client.get("/v1/rules")
    assert rules_resp.status_code == 200
    print("  /v1/rules: OK")

    # ------------------------------------------------------------------
    # Seed balances and rent housing
    # ------------------------------------------------------------------
    print_section("Housing setup")

    for name in AGENT_NAMES:
        amount = 15 if name == "eco_homeless" else 500
        await give_balance(app, name, amount)

    # Outskirts: gatherers and workers
    for name in ["eco_gatherer1", "eco_gatherer2", "eco_worker1", "eco_worker2", "eco_criminal"]:
        result = await agents[name].call("rent_housing", {"zone": "outskirts"})
        assert result["zone_slug"] == "outskirts"
        assert result["rent_cost_per_hour"] == 5.0

    # Industrial: mill owner, lumberjack
    for name in ["eco_miller", "eco_lumberjack"]:
        result = await agents[name].call("rent_housing", {"zone": "industrial"})
        assert result["zone_slug"] == "industrial"
        assert result["rent_cost_per_hour"] == 15.0

    # Suburbs: baker, trader, banker, politician
    for name in ["eco_baker", "eco_trader", "eco_banker", "eco_politician"]:
        result = await agents[name].call("rent_housing", {"zone": "suburbs"})
        assert result["zone_slug"] == "suburbs"
        assert result["rent_cost_per_hour"] == 25.0

    # eco_homeless stays homeless
    s = await agents["eco_homeless"].status()
    assert s["housing"]["homeless"] is True
    print("  5 outskirts, 2 industrial, 4 suburbs, 1 homeless")

    # Verify housed agents
    for name in ["eco_gatherer1", "eco_miller", "eco_baker"]:
        s = await agents[name].status()
        assert s["housing"]["homeless"] is False
        assert s["housing"]["zone_id"] is not None

    # ------------------------------------------------------------------
    # 2-day survival simulation
    # ------------------------------------------------------------------
    print_section("2-day survival simulation")

    balance_before = {}
    for name in AGENT_NAMES:
        balance_before[name] = await get_balance(app, name)

    await run_tick(hours=48)

    balance_after = {}
    for name in AGENT_NAMES:
        balance_after[name] = await get_balance(app, name)

    # All agents should have costs deducted
    for name in AGENT_NAMES:
        assert balance_after[name] < balance_before[name], (
            f"{name} balance should decrease: {balance_before[name]} -> {balance_after[name]}"
        )

    # Homeless spends less than suburbs resident
    homeless_spent = float(balance_before["eco_homeless"] - balance_after["eco_homeless"])
    baker_spent = float(balance_before["eco_baker"] - balance_after["eco_baker"])
    assert baker_spent > homeless_spent, (
        f"Suburbs renter should spend more ({baker_spent:.2f}) than homeless ({homeless_spent:.2f})"
    )
    print(f"  eco_homeless spent: {homeless_spent:.2f} (survival only)")
    print(f"  eco_baker spent: {baker_spent:.2f} (survival + suburbs rent)")

    # Transaction records
    async with app.state.session_factory() as session:
        food_count = (
            await session.execute(select(func.count()).select_from(Transaction).where(Transaction.type == "food"))
        ).scalar()
        rent_count = (
            await session.execute(select(func.count()).select_from(Transaction).where(Transaction.type == "rent"))
        ).scalar()
    assert food_count > 0, "No food transactions recorded"
    assert rent_count > 0, "No rent transactions recorded"
    print(f"  Transactions: {food_count} food, {rent_count} rent")

    # Events endpoint
    events_result = await agents["eco_baker"].call("events", {})
    assert "events" in events_result
    event_types = {e["type"] for e in events_result["events"]}
    assert "food_charged" in event_types, f"Expected food_charged in events, got {event_types}"
    assert "rent_charged" in event_types, f"Expected rent_charged in events, got {event_types}"
    print(f"  Events: {len(events_result['events'])} events, includes food_charged + rent_charged")

    print("\n  Bootstrap COMPLETE")
    return agents
