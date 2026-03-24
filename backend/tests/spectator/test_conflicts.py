"""
Spectator Conflict Detection Test

Verifies that the conflict detection system correctly identifies:
- Price wars (2+ businesses selling same good in same zone)
- Market cornering (one agent holds >50% of a good's supply)

Uses the same test architecture as the grand economy simulation.
"""

from __future__ import annotations

from tests.conftest import give_balance, give_inventory
from tests.helpers import TestAgent


async def run_conflicts_test(client, app, clock, run_tick, redis_client):
    """
    Conflict detection simulation.

    1. Sign up agents, give them balance and housing
    2. Both register bakeries in the same zone
    3. Both set storefront prices for bread
    4. Give one agent a large amount of iron_ore
    5. Run a tick
    6. Verify /api/conflicts returns price_war and market_cornering
    """

    # ── Phase 1: Set up agents ──
    alice = await TestAgent.signup(client, "Conf-Alice", model="TestModel")
    bob = await TestAgent.signup(client, "Conf-Bob", model="TestModel")

    await give_balance(app, "Conf-Alice", 5000)
    await give_balance(app, "Conf-Bob", 5000)

    # ── Phase 2: Housing ──
    await alice.call("rent_housing", {"zone": "suburbs"})
    await bob.call("rent_housing", {"zone": "suburbs"})

    # ── Phase 3: Register bakeries in the same zone ──
    alice_biz = await alice.call(
        "register_business",
        {"name": "Alice Bread Shop", "type": "bakery", "zone": "suburbs"},
    )
    alice_biz_id = alice_biz.get("business_id") or alice_biz.get("id")
    assert alice_biz_id, f"Alice business registration failed: {alice_biz}"

    bob_biz = await bob.call(
        "register_business",
        {"name": "Bob Bread Shop", "type": "bakery", "zone": "suburbs"},
    )
    bob_biz_id = bob_biz.get("business_id") or bob_biz.get("id")
    assert bob_biz_id, f"Bob business registration failed: {bob_biz}"

    # ── Phase 4: Set storefront prices for bread ──
    await alice.call("set_prices", {"business_id": alice_biz_id, "product": "bread", "price": 10})
    await bob.call("set_prices", {"business_id": bob_biz_id, "product": "bread", "price": 8})

    # ── Phase 5: Give Alice a lot of iron_ore for market cornering ──
    await give_inventory(app, "Conf-Alice", "iron_ore", 100)

    # ── Phase 6: Run a tick ──
    await run_tick()

    # Clear the Redis cache so conflict detection runs fresh
    await redis_client.delete("spectator:conflicts")

    # ── Phase 7: Verify /api/conflicts ──
    resp = await client.get("/api/conflicts")
    assert resp.status_code == 200, f"Conflicts endpoint failed: {resp.status_code} {resp.text[:200]}"

    data = resp.json()
    assert "conflicts" in data, f"No 'conflicts' key in response: {data}"

    conflicts = data["conflicts"]
    conflict_types = [c["type"] for c in conflicts]

    # Check for price war (two bakeries in suburbs with bread prices)
    assert "price_war" in conflict_types, (
        f"Expected price_war conflict but got types: {conflict_types}. Full: {conflicts}"
    )

    # Verify the price war details
    price_wars = [c for c in conflicts if c["type"] == "price_war"]
    assert any("bread" in pw["detail"] for pw in price_wars), f"Expected a price war involving bread: {price_wars}"

    # Check for market cornering (Alice has 100 iron_ore, which should be >50% of total)
    assert "market_cornering" in conflict_types, (
        f"Expected market_cornering conflict but got types: {conflict_types}. Full: {conflicts}"
    )

    # Verify market cornering details
    cornering = [c for c in conflicts if c["type"] == "market_cornering"]
    assert any("Conf-Alice" in mc["detail"] for mc in cornering), (
        f"Expected Conf-Alice in market cornering detail: {cornering}"
    )
    assert any("iron_ore" in mc["detail"] for mc in cornering), (
        f"Expected iron_ore in market cornering detail: {cornering}"
    )
