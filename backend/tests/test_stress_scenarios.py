"""
Economic Stress Tests for Agent Economy.

Two large-scale tests that push the simulation through extreme conditions:

1. test_economic_collapse_and_recovery
   - Phase 1: Build a thriving economy with 8 agents, businesses, and workers
   - Phase 2: Drain agent balances to trigger mass bankruptcy
   - Phase 3: Verify NPC gap-filling keeps the economy running; fresh agents can join

2. test_government_policy_transitions
   - Phase 1: Establish free_market baseline with 6 voting-age agents
   - Phase 2: Vote in authoritarian government, verify high taxes and enforcement
   - Phase 3: Vote in libertarian government, verify low taxes and enforcement
   - Phase 4: Final invariant checks

Both tests verify the "no negative inventory" invariant at every checkpoint
and exercise the full tick system through the real REST API.
"""

from __future__ import annotations

import pytest

from tests.helpers import TestAgent
from tests.stress.collapse_recovery import phase2_economic_crisis, phase3_recovery
from tests.stress.collapse_setup import phase1_build_economy
from tests.stress.government_setup import phase1_free_market
from tests.stress.government_transitions import (
    phase2_authoritarian,
    phase3_libertarian,
    phase4_final_checks,
)

# ---------------------------------------------------------------------------
# Test 1: Economic Collapse and Recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_economic_collapse_and_recovery(client, app, clock, run_tick, redis_client):
    """
    Simulate an economy that thrives, collapses via mass bankruptcy,
    and recovers through NPC gap-filling.
    """
    print(f"\n\n{'#' * 60}")
    print("# STRESS TEST: ECONOMIC COLLAPSE AND RECOVERY")
    print(f"# Start time: {clock.now().isoformat()}")
    print(f"{'#' * 60}")

    state = await phase1_build_economy(client, app, clock, run_tick)
    state = await phase2_economic_crisis(app, clock, run_tick, state)
    await phase3_recovery(client, app, clock, run_tick, state)

    print(f"\n{'=' * 60}")
    print("  STRESS TEST: Economic Collapse and Recovery -- PASSED")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Test 2: Government Policy Transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_government_policy_transitions(client, app, clock, run_tick, redis_client):
    """
    Test cascading effects of government policy changes across
    multiple election cycles: free_market -> authoritarian -> libertarian.
    """
    print(f"\n\n{'#' * 60}")
    print("# STRESS TEST: GOVERNMENT POLICY TRANSITIONS")
    print(f"# Start time: {clock.now().isoformat()}")
    print(f"{'#' * 60}")

    state = await phase1_free_market(client, app, clock, run_tick, redis_client)
    state = await phase2_authoritarian(app, clock, run_tick, redis_client, state)
    state = await phase3_libertarian(app, clock, run_tick, redis_client, state)
    await phase4_final_checks(app, state)

    print(f"\n{'=' * 60}")
    print("  STRESS TEST: Government Policy Transitions -- PASSED")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Test 3: Malformed JSON returns 400 instead of 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_returns_400(client, app, clock, run_tick, redis_client):
    """
    Regression test for Sentry issue 107538054: a literal newline inside a
    JSON string value caused an unhandled JSONDecodeError (500). The fix
    catches the error in _body_or_empty and returns INVALID_PARAMS (400).
    """
    agent = await TestAgent.signup(client, "json_tester")
    headers = {
        "Authorization": f"Bearer {agent.action_token}",
        "Content-Type": "application/json",
    }

    # Reproduce the exact payload from the Sentry trace: two UUIDs joined by
    # a literal newline inside business_id — invalid JSON.
    malformed_body = (
        b'{"action":"close_business","business_id":"'
        b"df23a5f9-64a2-4338-8008-1435a7276549"
        b"\n"
        b'ff544f88-35e4-4aef-80b4-ddd0a97dfd80"}'
    )

    response = await client.post("/v1/employees", content=malformed_body, headers=headers)
    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"

    body = response.json()
    assert body["error_code"] == "INVALID_PARAMS"
    assert "Invalid JSON" in body["message"]

    # Also verify completely unparseable garbage
    response2 = await client.post("/v1/employees", content=b"not json at all", headers=headers)
    assert response2.status_code == 400
    assert response2.json()["error_code"] == "INVALID_PARAMS"
