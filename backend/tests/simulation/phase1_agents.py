"""Phase 1: Bootstrap & Basics (Days 0-1) — signup, gathering, cooldowns, API discovery."""

from __future__ import annotations

from tests.helpers import TestAgent
from tests.simulation.helpers import AGENT_NAMES, print_phase, print_section


async def run_phase_1(client, app, clock, run_tick, redis_client):
    """Sign up agents, test gathering, verify API discovery.

    Returns the agents dict.
    """
    print_phase(1, "BOOTSTRAP & BASICS")

    # --- 1a: Sign up 12 agents with different intended roles ---
    print_section("Signing up 12 agents")

    agents: dict[str, TestAgent] = {}
    for name in AGENT_NAMES:
        agent = await TestAgent.signup(client, name)
        agents[name] = agent
        print(f"  Signed up: {name}")

    assert len(agents) == 12

    # --- 1b: Verify initial status ---
    print_section("Verifying initial status")
    for name, agent in agents.items():
        s = await agent.status()
        assert s["balance"] == 15.0, f"{name} balance should be 15, got {s['balance']}"
        assert s["housing"]["homeless"] is True, f"{name} should be homeless"
        assert s["bankruptcy_count"] == 0, f"{name} should have 0 bankruptcies"
    print("  All agents: balance=15, homeless, 0 bankruptcies")

    # --- 1c: Test gathering ---
    print_section("Testing gathering mechanics")

    g1 = agents["eco_gatherer1"]

    # Gather berries
    result = await g1.call("gather", {"resource": "berries"})
    assert result["gathered"] == "berries"
    assert result["quantity"] == 1
    assert result["cooldown_seconds"] == 25
    # Verify storage info in gather response
    assert "storage" in result, "Gather response should include storage info"
    assert "used" in result["storage"]
    assert "capacity" in result["storage"]
    assert "free" in result["storage"]
    assert result["storage"]["capacity"] == 100  # default agent storage
    print(f"  Gathered berries: qty=1, cooldown=25s, storage={result['storage']['used']}/{result['storage']['capacity']}")

    # Immediate retry: COOLDOWN_ACTIVE
    _, err = await g1.try_call("gather", {"resource": "berries"})
    assert err == "COOLDOWN_ACTIVE"
    print("  Same-resource cooldown enforced")

    # Different resource after global cooldown (5s)
    clock.advance(6)
    result2 = await g1.call("gather", {"resource": "wood"})
    assert result2["gathered"] == "wood"
    print("  Different resource OK after global cooldown")

    # Gather wheat and herbs
    clock.advance(6)
    await g1.call("gather", {"resource": "wheat"})
    clock.advance(6)
    await g1.call("gather", {"resource": "herbs"})
    print("  Gathered wheat and herbs")

    # --- 1d: Non-gatherable resource ---
    clock.advance(6)
    _, err = await g1.try_call("gather", {"resource": "bread"})
    assert err is not None, "bread should not be gatherable"
    print(f"  Non-gatherable resource rejected (error={err})")

    # --- 1e: API discovery checks ---
    print_section("Testing API discovery")

    # GET /v1/tools - endpoint catalog
    tools_resp = await client.get("/v1/tools")
    assert tools_resp.status_code == 200
    tools_body = tools_resp.json()
    assert tools_body["ok"] is True
    endpoints = tools_body["data"]["endpoints"]
    assert len(endpoints) >= 20, f"Expected at least 20 endpoints, got {len(endpoints)}"
    print(f"  /v1/tools returns {len(endpoints)} endpoints")

    # GET /v1/rules - game rules
    rules_resp = await client.get("/v1/rules")
    assert rules_resp.status_code == 200
    print(f"  /v1/rules returns game documentation")

    print("\n  Phase 1 COMPLETE")

    return agents
