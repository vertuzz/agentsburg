"""
Spectator Event Feed Test

Simulation-style test that exercises the spectator event feed through the
real REST API. Sets up a mini economy, triggers ticks that generate events,
and verifies the /api/feed and /api/pulse endpoints return properly
narrated events with correct drama levels and categories.

Uses the same test architecture as the grand economy simulation:
- Real API calls via httpx.ASGITransport
- Real DB and Redis (only MockClock is mocked)
- TestAgent helper for agent actions
"""

from __future__ import annotations

from tests.conftest import give_balance, give_inventory
from tests.helpers import TestAgent


async def run_spectator_feed_test(client, app, clock, run_tick, redis_client):
    """
    Full spectator event feed simulation.

    1. Sign up agents
    2. Set up an economy with businesses and marketplace activity
    3. Trigger slow tick (rent, food, taxes, audits, bankruptcy)
    4. Verify /api/feed returns events with narrative text
    5. Verify /api/pulse returns activity counts
    6. Verify drama and category filters work
    7. Verify business registration emits spectator events
    """

    # ── Phase 1: Set up agents ──
    alice = await TestAgent.signup(client, "Feed-Alice", model="ModelA")
    bob = await TestAgent.signup(client, "Feed-Bob", model="ModelB")
    await TestAgent.signup(client, "Feed-Charlie", model="ModelA")

    # Give agents starting capital
    await give_balance(app, "Feed-Alice", 5000)
    await give_balance(app, "Feed-Bob", 5000)
    await give_balance(app, "Feed-Charlie", 200)

    # ── Phase 2: Business registration (should emit spectator event) ──
    # Alice needs housing first
    await alice.call("rent_housing", {"zone": "suburbs"})
    biz_result = await alice.call(
        "register_business",
        {
            "name": "Alice Bakery",
            "type": "bakery",
            "zone": "suburbs",
        },
    )
    assert biz_result.get("business_id") or biz_result.get("id")

    # Bob also gets housing and a business
    await bob.call("rent_housing", {"zone": "downtown"})
    await bob.call(
        "register_business",
        {
            "name": "Bob Smithy",
            "type": "smithy",
            "zone": "industrial",
        },
    )

    # ── Phase 3: Marketplace activity ──
    # Give Alice some goods to sell
    await give_inventory(app, "Feed-Alice", "bread", 20)
    await alice.call(
        "marketplace_order",
        {
            "action": "sell",
            "product": "bread",
            "quantity": 10,
            "price": 15,
        },
    )

    # Bob places a buy order that should match Alice's sell
    await bob.call(
        "marketplace_order",
        {
            "action": "buy",
            "product": "bread",
            "quantity": 5,
            "price": 20,
        },
    )

    # ── Phase 4: Trigger ticks to generate events ──
    # Run a quick tick first (just fast tick with matching)
    await run_tick(minutes=1)
    # Then an hourly tick for slow tick events (rent, food, taxes)
    await run_tick(hours=1.1)

    # ── Phase 5: Verify /api/feed ──
    resp = await client.get("/api/feed")
    assert resp.status_code == 200
    feed_data = resp.json()

    assert "events" in feed_data
    assert "pulse" in feed_data
    events = feed_data["events"]

    # Should have events (rent, survival costs, marketplace fills, business registration, etc.)
    assert len(events) > 0, "Feed should have events after a tick cycle"

    # Verify event structure
    for ev in events:
        assert "type" in ev, "Event missing 'type'"
        assert "text" in ev, "Event missing 'text'"
        assert "drama" in ev, "Event missing 'drama'"
        assert "category" in ev, "Event missing 'category'"
        assert "ts" in ev, "Event missing 'ts'"
        assert ev["drama"] in ("routine", "notable", "critical"), f"Invalid drama: {ev['drama']}"
        assert ev["category"] in ("economy", "crime", "politics", "market", "business"), (
            f"Invalid category: {ev['category']}"
        )
        # Narrative text should be a non-empty string
        assert isinstance(ev["text"], str) and len(ev["text"]) > 0, "Event text should be non-empty"

    # ── Phase 6: Verify specific event types appeared ──
    event_types = {ev["type"] for ev in events}

    # Business registration should be in the feed
    assert "business_registered" in event_types, f"Expected 'business_registered' event. Got types: {event_types}"

    # Survival costs should be in the feed (routine event from slow tick)
    assert "survival_costs" in event_types, f"Expected 'survival_costs' event. Got types: {event_types}"

    # Marketplace fill should be in the feed (Bob's buy matched Alice's sell)
    assert "marketplace_fill" in event_types, f"Expected 'marketplace_fill' event. Got types: {event_types}"

    # Check that the marketplace fill has sensible narrative
    fill_events = [ev for ev in events if ev["type"] == "marketplace_fill"]
    assert len(fill_events) > 0
    fill_text = fill_events[0]["text"]
    assert "bread" in fill_text.lower() or "Feed-" in fill_text, (
        f"Fill narrative should mention the good or agent: {fill_text}"
    )

    # Check business registration narrative (feed is newest-first, so check any)
    biz_events = [ev for ev in events if ev["type"] == "business_registered"]
    assert len(biz_events) >= 1
    biz_texts = " ".join(ev["text"] for ev in biz_events)
    assert "Alice Bakery" in biz_texts or "Feed-Alice" in biz_texts

    # ── Phase 7: Verify /api/pulse ──
    pulse_resp = await client.get("/api/pulse")
    assert pulse_resp.status_code == 200
    pulse = pulse_resp.json()
    assert "count_1h" in pulse
    assert "count_24h" in pulse
    assert pulse["count_1h"] > 0, "Should have events in the last hour"
    assert pulse["count_24h"] >= pulse["count_1h"]

    # ── Phase 8: Verify drama filtering ──
    # notable+ should exclude routine events
    resp_notable = await client.get("/api/feed", params={"min_drama": "notable"})
    assert resp_notable.status_code == 200
    notable_events = resp_notable.json()["events"]
    for ev in notable_events:
        assert ev["drama"] in ("notable", "critical"), (
            f"Notable filter should exclude routine events, got: {ev['drama']}"
        )

    # critical only
    resp_critical = await client.get("/api/feed", params={"min_drama": "critical"})
    assert resp_critical.status_code == 200
    critical_events = resp_critical.json()["events"]
    for ev in critical_events:
        assert ev["drama"] == "critical", f"Critical filter failed, got: {ev['drama']}"

    # ── Phase 9: Verify category filtering ──
    resp_market = await client.get("/api/feed", params={"category": "market"})
    assert resp_market.status_code == 200
    market_events = resp_market.json()["events"]
    for ev in market_events:
        assert ev["category"] == "market", f"Market category filter failed, got: {ev['category']}"

    resp_business = await client.get("/api/feed", params={"category": "business"})
    assert resp_business.status_code == 200
    business_events = resp_business.json()["events"]
    for ev in business_events:
        assert ev["category"] == "business", f"Business category filter failed, got: {ev['category']}"

    # ── Phase 10: Force bankruptcy and verify critical event ──
    # Charlie has low balance — push them below threshold
    await give_balance(app, "Feed-Charlie", -250)
    await run_tick(hours=1.1)

    # Check feed for bankruptcy event
    resp_after = await client.get("/api/feed")
    all_events = resp_after.json()["events"]
    event_types_after = {ev["type"] for ev in all_events}
    assert "bankruptcy_summary" in event_types_after, (
        f"Expected 'bankruptcy_summary' after forced bankruptcy. Got: {event_types_after}"
    )

    # Bankruptcy should be critical
    bankruptcy_events = [ev for ev in all_events if ev["type"] == "bankruptcy_summary"]
    assert len(bankruptcy_events) > 0
    assert bankruptcy_events[0]["drama"] == "critical"
    assert "Feed-Charlie" in bankruptcy_events[0]["text"]
