"""
Spectator Strategy & Badges Test (Phase 2)

Verifies that the /api/agents/{id} endpoint returns strategy classification
(dict with strategy + traits) and badges (list of badge dicts), and that
/api/agents list includes a strategy string per agent.

Sets up agents with distinct economic profiles and checks that the
classification system correctly identifies their behaviour.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.government import Violation
from tests.conftest import give_balance, give_inventory
from tests.helpers import TestAgent


async def run_strategy_badges_test(client, app, clock, run_tick, redis_client):
    """
    Phase 2 spectator test: strategy classification and badges.

    1. Sign up 3 agents with different profiles
    2. Set up economy (housing, business, marketplace, violations)
    3. Run a tick
    4. Verify /api/agents/{id} returns strategy dict and badges list
    5. Verify /api/agents list returns strategy string per agent
    """

    # ── Step 1: Sign up agents ──
    alice = await TestAgent.signup(client, "Strat-Alice", model="ModelA")
    bob = await TestAgent.signup(client, "Strat-Bob", model="ModelB")
    charlie = await TestAgent.signup(client, "Strat-Charlie", model="ModelC")

    # ── Step 2: Give all agents starting capital ──
    await give_balance(app, "Strat-Alice", 5000)
    await give_balance(app, "Strat-Bob", 5000)
    await give_balance(app, "Strat-Charlie", 5000)

    # ── Step 3: Agent A — business owner ──
    await alice.call("rent_housing", {"zone": "suburbs"})
    biz_result = await alice.call(
        "register_business",
        {
            "name": "Strat Bakery",
            "type": "bakery",
            "zone": "suburbs",
        },
    )
    assert biz_result.get("business_id") or biz_result.get("id"), f"Alice business registration failed: {biz_result}"

    # ── Step 4: Agent B — marketplace trader ──
    await bob.call("rent_housing", {"zone": "suburbs"})
    await give_inventory(app, "Strat-Bob", "berries", 20)
    await bob.call(
        "marketplace_order",
        {
            "action": "sell",
            "product": "berries",
            "quantity": 10,
            "price": 12,
        },
    )

    # ── Step 5: Agent C — tax evader with violations ──
    await charlie.call("rent_housing", {"zone": "suburbs"})

    # Create a Violation record directly in DB
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == "Strat-Charlie"))
        agent_c = result.scalar_one()
        agent_c_id = agent_c.id

        violation = Violation(
            agent_id=agent_c_id,
            type="tax_evasion",
            amount_evaded=100,
            fine_amount=50,
            detected_at=clock.now(),
        )
        session.add(violation)

        # Increment the agent's violation_count so strategy classification picks it up
        agent_c.violation_count = (agent_c.violation_count or 0) + 1
        await session.commit()

    # ── Step 6: Run a tick to process things ──
    await run_tick(hours=1.1)

    # ── Step 7: Look up agent IDs from /api/agents ──
    agents_resp = await client.get("/api/agents")
    assert agents_resp.status_code == 200, f"GET /api/agents failed: {agents_resp.text}"
    agents_data = agents_resp.json()
    assert "agents" in agents_data

    agent_id_map = {}
    for a in agents_data["agents"]:
        agent_id_map[a["name"]] = a["id"]

    alice_id = agent_id_map["Strat-Alice"]
    bob_id = agent_id_map["Strat-Bob"]
    charlie_id = agent_id_map["Strat-Charlie"]

    # ── Step 8: Verify /api/agents list has strategy field ──
    for a in agents_data["agents"]:
        if a["name"].startswith("Strat-"):
            assert "strategy" in a, f"Agent list entry missing 'strategy': {a}"
            # strategy in list view is a string (or null)
            assert a["strategy"] is None or isinstance(a["strategy"], str), (
                f"Agent list strategy should be string or null, got: {type(a['strategy'])}"
            )

    # ── Step 9: Verify /api/agents/{id} for Agent A (business owner) ──
    alice_resp = await client.get(f"/api/agents/{alice_id}")
    assert alice_resp.status_code == 200, f"GET /api/agents/{alice_id} failed: {alice_resp.text}"
    alice_profile = alice_resp.json()

    # strategy should be a dict with "strategy" (str) and "traits" (list)
    assert "strategy" in alice_profile, f"Profile missing 'strategy': {alice_profile.keys()}"
    assert isinstance(alice_profile["strategy"], dict), (
        f"Profile strategy should be dict, got: {type(alice_profile['strategy'])}"
    )
    assert "strategy" in alice_profile["strategy"], f"Strategy dict missing 'strategy' key: {alice_profile['strategy']}"
    assert "traits" in alice_profile["strategy"], f"Strategy dict missing 'traits' key: {alice_profile['strategy']}"
    assert isinstance(alice_profile["strategy"]["strategy"], str)
    assert isinstance(alice_profile["strategy"]["traits"], list)

    # Alice should have "business_owner" trait
    assert "business_owner" in alice_profile["strategy"]["traits"], (
        f"Alice should have 'business_owner' trait, got: {alice_profile['strategy']['traits']}"
    )

    # badges should be a list
    assert "badges" in alice_profile, f"Profile missing 'badges': {alice_profile.keys()}"
    assert isinstance(alice_profile["badges"], list), f"Badges should be list, got: {type(alice_profile['badges'])}"

    # Alice should have "first_business" badge
    alice_badge_slugs = [b["slug"] for b in alice_profile["badges"]]
    assert "first_business" in alice_badge_slugs, f"Alice should have 'first_business' badge, got: {alice_badge_slugs}"

    # Verify badge structure
    for badge in alice_profile["badges"]:
        assert "slug" in badge, f"Badge missing 'slug': {badge}"
        assert "name" in badge, f"Badge missing 'name': {badge}"
        assert "description" in badge, f"Badge missing 'description': {badge}"

    # ── Step 10: Verify /api/agents/{id} for Agent C (tax evader) ──
    charlie_resp = await client.get(f"/api/agents/{charlie_id}")
    assert charlie_resp.status_code == 200
    charlie_profile = charlie_resp.json()

    assert isinstance(charlie_profile["strategy"], dict)
    assert isinstance(charlie_profile["strategy"]["traits"], list)
    assert "tax_evader" in charlie_profile["strategy"]["traits"], (
        f"Charlie should have 'tax_evader' trait, got: {charlie_profile['strategy']['traits']}"
    )

    assert isinstance(charlie_profile["badges"], list)
    charlie_badge_slugs = [b["slug"] for b in charlie_profile["badges"]]
    assert "tax_evader" in charlie_badge_slugs, f"Charlie should have 'tax_evader' badge, got: {charlie_badge_slugs}"

    # ── Step 11: Verify Agent B profile structure ──
    bob_resp = await client.get(f"/api/agents/{bob_id}")
    assert bob_resp.status_code == 200
    bob_profile = bob_resp.json()

    assert isinstance(bob_profile["strategy"], dict)
    assert "strategy" in bob_profile["strategy"]
    assert "traits" in bob_profile["strategy"]
    assert isinstance(bob_profile["strategy"]["strategy"], str)
    assert isinstance(bob_profile["strategy"]["traits"], list)
    assert isinstance(bob_profile["badges"], list)
