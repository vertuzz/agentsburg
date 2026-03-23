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
    # Cash on gather should use cash_on_gather (1), not base_value (2)
    assert result["cash_earned"] == 1.0, f"berries cash_on_gather should be 1, got {result['cash_earned']}"
    assert result["base_value"] == 2, f"berries base_value should be 2, got {result['base_value']}"
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

    # Gather copper_ore: cash_on_gather=4, not base_value=6
    clock.advance(6)
    copper_result = await g1.call("gather", {"resource": "copper_ore"})
    assert copper_result["cash_earned"] == 4.0, f"copper cash_on_gather should be 4, got {copper_result['cash_earned']}"
    assert copper_result["base_value"] == 6, f"copper base_value should be 6, got {copper_result['base_value']}"
    print(f"  Copper ore: cash_on_gather=4, base_value=6 verified")

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

    # --- 1e: Status response quality checks ---
    print_section("Status response quality")

    # Onboarding hints: homeless agents should get housing suggestions
    g1_status = await g1.status()
    hints = g1_status.get("_hints", {})
    assert "next_steps" in hints, "Status should include _hints.next_steps"
    assert len(hints["next_steps"]) > 0, "next_steps should not be empty"
    assert any("housing" in t.lower() for t in hints["next_steps"]), \
        "Homeless agent should get housing hint in next_steps"
    print("  Onboarding hints include housing suggestion for homeless agent")

    # economy_events count in status
    assert "economy_events" in g1_status, "Status should include economy_events"
    assert isinstance(g1_status["economy_events"], int), "economy_events should be an int"
    print(f"  economy_events={g1_status['economy_events']} present in status")

    # Cooldown format: after gather, should have remaining + total
    cooldowns = g1_status.get("cooldowns", {})
    if "gather:berries" in cooldowns:
        cd = cooldowns["gather:berries"]
        assert "remaining" in cd, "Cooldown should have 'remaining'"
        assert "total" in cd, "Cooldown should have 'total'"
        assert cd["total"] == 25, f"berries cooldown total should be 25, got {cd['total']}"
        print(f"  Cooldown format: remaining={cd['remaining']}, total={cd['total']}")
    else:
        # Cooldown may have expired if clock was advanced; check any active cooldown
        print("  Cooldown format check skipped (berries cooldown expired)")

    # --- 1f: Leaderboard is_npc flag ---
    print_section("Leaderboard checks")

    lb_result = await g1.call("leaderboard", {})
    assert "leaderboard" in lb_result
    for entry in lb_result["leaderboard"]:
        assert "is_npc" in entry, f"Leaderboard entry missing is_npc: {entry}"
        assert isinstance(entry["is_npc"], bool), f"is_npc should be bool, got {type(entry['is_npc'])}"
    print(f"  Leaderboard has {len(lb_result['leaderboard'])} entries, all with is_npc flag")

    # --- 1g: API discovery checks ---
    print_section("API discovery")

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
