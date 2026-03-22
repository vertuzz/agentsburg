"""
Tests for Sybil attack vector and NPC exploitation fixes.

1. Rate limiting uses direct client IP, not x-forwarded-for header
2. NPC supply calculation includes marketplace sell orders
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, func

from backend.models.agent import Agent
from backend.models.inventory import InventoryItem
from backend.models.marketplace import MarketOrder
from tests.conftest import give_balance
from tests.helpers import TestAgent


# ---------------------------------------------------------------------------
# 1. Rate limiting uses direct client IP, not x-forwarded-for
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_ignores_x_forwarded_for(client, app, clock, redis_client):
    """
    Verify that rate limiting uses the direct TCP connection IP
    (request.client.host) rather than trusting the x-forwarded-for header.

    An attacker spoofing x-forwarded-for should NOT get a separate rate
    limit bucket -- all requests from the same real IP share one bucket.

    We pre-seed the Redis rate limit counter to 5 (the signup limit),
    then send one more request with a spoofed x-forwarded-for.
    If the code trusted x-forwarded-for, the spoofed IP would get
    its own bucket and the request would succeed. Since we ignore
    x-forwarded-for, it hits the existing bucket and gets rate-limited.
    """
    # Enable rate limiting for this test
    app.state.rate_limit_enabled = True

    try:
        # The rate limiter uses "testclient" as the IP for ASGI transport.
        # Pre-seed the signup rate limit counter to exactly 5 (the limit).
        # The key format is: ratelimit:ip:{client_ip}:signup
        # With ASGI transport, request.client.host is typically "testclient"
        # We need to figure out the actual IP used. Do one real request first.
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "signup",
                    "arguments": {"name": "ip_probe"},
                },
                "id": "probe",
            },
        )
        # That was request #1. Set the counter to 5 to exhaust the limit.
        # The key is based on the real connection IP.
        # For httpx ASGI transport the IP is "127.0.0.1" (request.client.host)
        real_ip = "127.0.0.1"
        await redis_client.set(f"ratelimit:ip:{real_ip}:signup", "5")
        await redis_client.expire(f"ratelimit:ip:{real_ip}:signup", 60)

        # Now send a request with a DIFFERENT x-forwarded-for header.
        # If the code trusted x-forwarded-for, this would use IP "10.99.99.99"
        # which has no rate limit counter, so it would succeed.
        # Since we ignore x-forwarded-for, it uses "testclient" which is at 5,
        # and the next request (incrementing to 6) will exceed the limit of 5.
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "signup",
                    "arguments": {"name": "sybil_spoof"},
                },
                "id": "spoof_test",
            },
            headers={"x-forwarded-for": "10.99.99.99"},
        )
        result = resp.json()

        assert result.get("error") is not None, (
            "Request with spoofed x-forwarded-for should be rate-limited. "
            "If this passes, x-forwarded-for may be incorrectly trusted."
        )
        assert result["error"]["code"] == -32029  # RATE_LIMITED code
    finally:
        # Restore test default
        app.state.rate_limit_enabled = False


# ---------------------------------------------------------------------------
# 2. NPC supply calculation includes marketplace sell orders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_npc_supply_includes_marketplace_sell_orders(
    client, app, clock, db, redis_client
):
    """
    Verify that the NPC supply gap calculation counts active marketplace
    sell orders in addition to business inventory.

    Without this fix, players could hoard goods off-market to trigger
    NPC business spawning even when supply exists on the order book.
    """
    from backend.economy.npc_businesses import simulate_npc_businesses

    settings = app.state.settings

    # Find a good with NPC demand configured
    demand_entries = settings.npc_demand.get("npc_demand", [])
    if not demand_entries:
        pytest.skip("No NPC demand configuration found")

    # Pick the first demand entry with significant demand
    target_good = None
    base_demand = 0
    for entry in demand_entries:
        d = float(entry.get("base_demand_per_zone", 0))
        if d >= 10:
            target_good = entry["good"]
            base_demand = d
            break

    if target_good is None:
        pytest.skip("No good with base_demand >= 10 found in npc_demand config")

    # Create a test agent and give them resources to place sell orders
    agent = await TestAgent.signup(client, "supply_tester")
    await give_balance(app, "supply_tester", 10000)

    # Get the agent's DB record
    async with app.state.session_factory() as session:
        agent_result = await session.execute(
            select(Agent).where(Agent.name == "supply_tester")
        )
        agent_row = agent_result.scalar_one()
        agent_id = agent_row.id

        # Count zones
        from backend.models.zone import Zone
        zones_result = await session.execute(select(func.count(Zone.id)))
        num_zones = zones_result.scalar() or 1

    # Calculate how much supply we need to place on the marketplace
    # to exceed the spawn threshold (0.5 ticks' worth)
    needed_supply = int(base_demand * num_zones * 1.0) + 1  # > 0.5 ticks' worth

    # Directly insert sell orders into the marketplace for this good
    async with app.state.session_factory() as session:
        # First add inventory to the agent so we can place sell orders
        inv_item = InventoryItem(
            owner_type="agent",
            owner_id=agent_id,
            good_slug=target_good,
            quantity=needed_supply,
        )
        session.add(inv_item)
        await session.flush()

        # Create a sell order that locks those goods
        sell_order = MarketOrder(
            agent_id=agent_id,
            good_slug=target_good,
            side="sell",
            quantity_total=needed_supply,
            quantity_filled=0,
            price=10.00,
            status="open",
        )
        session.add(sell_order)
        await session.commit()

    # Run NPC business simulation
    async with app.state.session_factory() as session:
        result = await simulate_npc_businesses(session, clock, settings)
        await session.commit()

    # Check that no NPC was spawned for target_good
    # (because marketplace sell orders provide enough supply)
    spawned_for_good = []
    if "spawned" in result:
        spawned_for_good = [
            s for s in result["spawned"]
            if s.get("good") == target_good or s.get("recipe_output") == target_good
        ]

    assert len(spawned_for_good) == 0, (
        f"NPC should NOT have been spawned for {target_good} because "
        f"marketplace sell orders provide sufficient supply. "
        f"Spawned: {spawned_for_good}"
    )
