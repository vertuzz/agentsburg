"""
NPC Simulation Test

Comprehensive test for the redesigned NPC system: dynamic scaling,
smart pricing, feed exclusion, stats toggle, and survival cost exemption.

Run with: cd backend && uv run pytest tests/test_npc_simulation.py -v
"""

from __future__ import annotations

import pytest

from tests.simulation.npc import run_npc_simulation


@pytest.mark.asyncio
async def test_npc_simulation(client, app, clock, run_tick, redis_client):
    """
    NPC simulation: bootstrap, scaling, feed exclusion, stats toggle,
    competition retreat, wage scaling, survival exemption.
    """
    await run_npc_simulation(client, app, clock, run_tick, redis_client)
