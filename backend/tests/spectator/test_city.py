"""
City Visualization Endpoint Test

Verifies the /api/city endpoint returns the correct schema with zones,
agent activities, sector GDP aggregation, and figurine scaling data.

Uses the same test architecture as the spectator tests:
- Real API calls via httpx.ASGITransport
- Real DB and Redis (only MockClock is mocked)
- TestAgent helper for agent actions
"""

from __future__ import annotations

from tests.conftest import deactivate_agent, give_balance, give_inventory, jail_agent
from tests.helpers import TestAgent


async def run_city_test(client, app, clock, run_tick, redis_client):
    """
    Full city visualization endpoint test.

    1. Sign up agents with varied states (active, jailed, homeless)
    2. Set up businesses in different zones/sectors
    3. Generate marketplace activity for GDP
    4. Verify /api/city returns correct schema
    5. Verify activity classification priority ordering
    6. Verify sector GDP aggregation
    7. Verify figurine scale computation
    8. Verify Redis caching works
    """

    # ── Phase 1: Set up agents ──
    alice = await TestAgent.signup(client, "City-Alice", model="ModelA")
    bob = await TestAgent.signup(client, "City-Bob", model="ModelB")
    await TestAgent.signup(client, "City-Charlie", model="ModelA")
    dave = await TestAgent.signup(client, "City-Dave", model="ModelB")

    # Give agents starting capital
    await give_balance(app, "City-Alice", 5000)
    await give_balance(app, "City-Bob", 5000)
    await give_balance(app, "City-Charlie", 200)
    await give_balance(app, "City-Dave", 1000)

    # ── Phase 2: Housing and businesses ──
    await alice.call("rent_housing", {"zone": "suburbs"})
    biz_result = await alice.call(
        "register_business",
        {"name": "Alice Bakery", "type": "bakery", "zone": "suburbs"},
    )
    assert biz_result.get("business_id") or biz_result.get("id")

    await bob.call("rent_housing", {"zone": "downtown"})
    await bob.call(
        "register_business",
        {"name": "Bob Smithy", "type": "smithy", "zone": "industrial"},
    )

    await dave.call("rent_housing", {"zone": "industrial"})

    # Charlie stays homeless (no housing)

    # ── Phase 3: Generate marketplace activity ──
    await give_inventory(app, "City-Alice", "bread", 20)
    await alice.call(
        "marketplace_order",
        {"action": "sell", "product": "bread", "quantity": 10, "price": 15},
    )
    await bob.call(
        "marketplace_order",
        {"action": "buy", "product": "bread", "quantity": 5, "price": 20},
    )

    # Run a tick to process orders and generate transactions
    await run_tick(minutes=1)

    # Jail Charlie for testing jailed activity
    await jail_agent(app, "City-Charlie", clock, hours=2.0)

    # ── Phase 4: Verify /api/city response schema ──
    resp = await client.get("/api/city")
    assert resp.status_code == 200
    data = resp.json()

    # Top-level keys
    assert "zones" in data
    assert "economy" in data
    assert "scale" in data
    assert "cached_at" in data

    # ── Verify zones structure ──
    zones = data["zones"]
    assert len(zones) >= 1, "Should have at least one zone"

    # The config should have standard zones
    for zone in zones:
        assert "slug" in zone
        assert "name" in zone
        assert "rent_cost" in zone
        assert "foot_traffic" in zone
        assert "gdp_6h" in zone
        assert "gdp_share" in zone
        assert "population" in zone
        assert "businesses" in zone
        assert "agents" in zone

        # Business breakdown
        biz = zone["businesses"]
        assert "total" in biz
        assert "npc" in biz
        assert "agent" in biz
        assert "by_sector" in biz
        assert set(biz["by_sector"].keys()) == {"extraction", "manufacturing", "retail", "services"}

        # GDP share should be between 0 and 1
        assert 0 <= zone["gdp_share"] <= 1.0

    # ── Verify agent activity classification ──
    all_agents_in_zones = []
    for zone in zones:
        all_agents_in_zones.extend(zone["agents"])

    # Find specific agents and check activities
    for agent_data in all_agents_in_zones:
        assert "id" in agent_data
        assert "name" in agent_data
        assert "activity" in agent_data
        assert "activity_detail" in agent_data
        assert "wealth_tier" in agent_data
        assert "is_jailed" in agent_data
        assert agent_data["activity"] in (
            "working",
            "gathering",
            "trading",
            "managing",
            "employed",
            "idle",
            "jailed",
            "homeless",
            "negotiating",
            "inactive",
        )

    # Charlie should be jailed
    charlie_agents = [a for a in all_agents_in_zones if a["name"] == "City-Charlie"]
    if charlie_agents:
        assert charlie_agents[0]["activity"] == "jailed"
        assert charlie_agents[0]["is_jailed"] is True

    # ── Verify economy section ──
    economy = data["economy"]
    assert "total_gdp_6h" in economy
    assert "population" in economy
    assert "sectors" in economy
    assert economy["population"] >= 4  # At least our 4 agents

    sectors = economy["sectors"]
    assert set(sectors.keys()) == {"extraction", "manufacturing", "retail", "services"}
    for sector_name, sector_info in sectors.items():
        assert "gdp" in sector_info
        assert "share" in sector_info
        assert "businesses" in sector_info
        assert "workers" in sector_info
        assert sector_info["gdp"] >= 0
        assert 0 <= sector_info["share"] <= 1.0

    # Alice has a bakery (retail) and Bob has a smithy (manufacturing)
    assert sectors["retail"]["businesses"] >= 1, "Should have retail businesses (bakery)"
    assert sectors["manufacturing"]["businesses"] >= 1, "Should have mfg businesses (smithy)"

    # ── Verify scale ──
    scale = data["scale"]
    assert "population" in scale
    assert "figurine_ratio" in scale
    assert "figurine_count" in scale
    assert scale["population"] == economy["population"]
    # With < 100 agents, ratio should be 1:1
    assert scale["figurine_ratio"] == 1
    assert scale["figurine_count"] == scale["population"]

    # ── Phase 5: Verify Redis caching ──
    # Second call should return cached data
    resp2 = await client.get("/api/city")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["cached_at"] == data["cached_at"], "Second call should return cached data"

    # ── Phase 6: Verify inactive agents excluded from population ──
    await deactivate_agent(app, "City-Dave")
    # Clear cache to get fresh data
    await redis_client.delete("city:visualization")

    resp3 = await client.get("/api/city")
    assert resp3.status_code == 200
    data3 = resp3.json()
    # Population should be one less now
    assert data3["economy"]["population"] == data["economy"]["population"] - 1

    # Dave should not appear in any zone's agents
    all_agents_after = []
    for zone in data3["zones"]:
        all_agents_after.extend(zone["agents"])
    dave_agents = [a for a in all_agents_after if a["name"] == "City-Dave"]
    assert len(dave_agents) == 0, "Inactive agents should not appear in city view"
