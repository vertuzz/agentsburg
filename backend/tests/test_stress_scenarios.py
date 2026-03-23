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
async def test_economic_collapse_and_recovery(
    client, app, clock, run_tick, redis_client
):
    """
    Simulate an economy that thrives, collapses via mass bankruptcy,
    and recovers through NPC gap-filling.
    """
    print(f"\n\n{'#'*60}")
    print("# STRESS TEST: ECONOMIC COLLAPSE AND RECOVERY")
    print(f"# Start time: {clock.now().isoformat()}")
    print(f"{'#'*60}")

    state = await phase1_build_economy(client, app, clock, run_tick)
    state = await phase2_economic_crisis(app, clock, run_tick, state)
    await phase3_recovery(client, app, clock, run_tick, state)

    print(f"\n{'='*60}")
    print("  STRESS TEST: Economic Collapse and Recovery -- PASSED")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Test 2: Government Policy Transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_government_policy_transitions(
    client, app, clock, run_tick, redis_client
):
    """
    Test cascading effects of government policy changes across
    multiple election cycles: free_market -> authoritarian -> libertarian.
    """
    print(f"\n\n{'#'*60}")
    print("# STRESS TEST: GOVERNMENT POLICY TRANSITIONS")
    print(f"# Start time: {clock.now().isoformat()}")
    print(f"{'#'*60}")

    state = await phase1_free_market(client, app, clock, run_tick, redis_client)
    state = await phase2_authoritarian(app, clock, run_tick, redis_client, state)
    state = await phase3_libertarian(app, clock, run_tick, redis_client, state)
    await phase4_final_checks(app, state)

    print(f"\n{'='*60}")
    print("  STRESS TEST: Government Policy Transitions -- PASSED")
    print(f"{'='*60}")
