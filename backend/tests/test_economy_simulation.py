"""
Grand Economy Simulation Test

THE comprehensive end-to-end test for the Agent Economy. If this test passes,
the application works. Covers every feature including adversarial edge cases:

1. Bootstrap — signup (+ XSS prevention), gathering, cooldowns, housing, survival
2. Business & Employment — registration, production, hiring, inventory, edge cases
3. Marketplace & Trading — orders, matching, concurrency safety, direct trades, messaging
4. Finance & Law — banking, loans, government, elections, jail, deactivation
5. Endgame — bankruptcy, deposit seizure, recovery, economy stats, invariant checks

12 agents, 28+ simulated days, every tool exercised, all edge cases covered.
All tests go through the real REST API via httpx ASGI transport.
The ONLY mock is MockClock.
"""

from __future__ import annotations

import pytest

from tests.simulation.bootstrap import run_bootstrap
from tests.simulation.business import run_business
from tests.simulation.endgame import run_endgame
from tests.simulation.finance_and_law import run_finance_and_law
from tests.simulation.trading import run_trading


@pytest.mark.asyncio
async def test_grand_economy_simulation(client, app, clock, run_tick, redis_client):
    """
    The grand economy simulation: a single massive test covering ALL features.

    12 agents, 28+ simulated days, every tool exercised, adversarial edge cases included.
    """

    agents = await run_bootstrap(client, app, clock, run_tick, redis_client)
    await run_business(agents, client, app, clock, run_tick, redis_client)
    await run_trading(agents, client, app, clock, run_tick, redis_client)
    await run_finance_and_law(agents, client, app, clock, run_tick, redis_client)
    await run_endgame(agents, client, app, clock, run_tick, redis_client)
