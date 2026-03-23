"""
Grand Economy Simulation Test

THE comprehensive end-to-end test for the Agent Economy. If this test passes,
the application works. Covers all 8 phases of the economy lifecycle:

Phase 1: Bootstrap & Basics (signup, gathering, cooldowns, API discovery)
Phase 2: Housing & Survival (rent, survival costs, tick deductions)
Phase 3: Business & Employment (register, produce, hire, work, commute)
Phase 4: Marketplace (order book, matching, cancellation, market orders)
Phase 5: Direct Trading (propose, accept, reject, cancel, messaging)
Phase 6: Banking (deposit, withdraw, loans, interest, installments)
Phase 7: Government & Law (voting, elections, taxes, audits, jail)
Phase 8: Bankruptcy & Recovery (liquidation, serial bankruptcy, NPC fill, economy stats)

All tests go through the real REST API via httpx ASGI transport.
The ONLY mock is MockClock.
"""

from __future__ import annotations

import pytest

from tests.simulation.phase1_agents import run_phase_1
from tests.simulation.phase2_housing import run_phase_2
from tests.simulation.phase3_business import run_phase_3
from tests.simulation.phase4_marketplace import run_phase_4
from tests.simulation.phase5_trading import run_phase_5
from tests.simulation.phase6_banking import run_phase_6
from tests.simulation.phase7_government import run_phase_7
from tests.simulation.phase8_endgame import run_phase_8


@pytest.mark.asyncio
async def test_grand_economy_simulation(client, app, clock, run_tick, redis_client):
    """
    The grand economy simulation: a single massive test covering ALL features.

    12 agents, 28 simulated days, every tool exercised.
    """

    agents = await run_phase_1(client, app, clock, run_tick, redis_client)
    await run_phase_2(agents, client, app, clock, run_tick, redis_client)
    await run_phase_3(agents, client, app, clock, run_tick, redis_client)
    await run_phase_4(agents, client, app, clock, run_tick, redis_client)
    await run_phase_5(agents, client, app, clock, run_tick, redis_client)
    await run_phase_6(agents, client, app, clock, run_tick, redis_client)
    await run_phase_7(agents, client, app, clock, run_tick, redis_client)
    await run_phase_8(agents, client, app, clock, run_tick, redis_client)
