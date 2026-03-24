"""
Adversarial, security, and edge case tests for Agent Economy.

Covers:
  1. Input validation & XSS prevention
  2. Cooldown enforcement
  3. Concurrency - double-spend prevention (balance)
  4. Concurrency - double-spend prevention (inventory)
  5. Concurrency - TOCTOU on trade response
  6. Wash trading prevention
  7. Storage full handling
  8. Cancel order fee
  9. Jail restrictions
  10. Bankruptcy deposit seizure
  11. Serial bankruptcy loan denial
  12. Vote persistence across elections
  13. Money supply conservation & negative inventory check
  14. Agent deactivation after max bankruptcies
  15. Business inventory transfer edge cases

All tests use real HTTP through the REST API. The only mock is MockClock.

Sections are grouped into separate modules under tests/adversarial/ and called
in order so that shared state (agents dict) flows through each phase.
"""

from __future__ import annotations

import pytest

from tests.adversarial.auth_and_input import run_auth_and_input
from tests.adversarial.bankruptcy_and_government import run_bankruptcy_and_government
from tests.adversarial.business_transfers import run_business_transfers
from tests.adversarial.concurrency import run_concurrency
from tests.adversarial.marketplace_and_jail import run_marketplace_and_jail


@pytest.mark.asyncio
async def test_adversarial_scenarios(client, app, clock, run_tick, db, redis_client):
    """
    Comprehensive adversarial, security, and edge case test.

    Exercises input validation, concurrency safety, storage limits,
    jail restrictions, bankruptcy mechanics, election persistence,
    and money supply conservation.
    """

    agents = {}

    # Sections 1-2: Input validation, XSS, cooldowns
    agents = await run_auth_and_input(client, app, clock, agents)

    # Sections 3-5: Concurrency and double-spend prevention
    agents = await run_concurrency(client, app, clock, agents)

    # Sections 6-9: Marketplace exploits and jail restrictions
    agents = await run_marketplace_and_jail(client, app, clock, run_tick, agents)

    # Sections 10-14: Bankruptcy, elections, money supply, deactivation
    agents = await run_bankruptcy_and_government(
        client,
        app,
        clock,
        run_tick,
        redis_client,
        agents,
    )

    # Section 15: Business inventory transfer edge cases
    agents = await run_business_transfers(client, app, clock, agents)

    # ===================================================================
    # Summary
    # ===================================================================
    print("\n" + "=" * 60)
    print("ALL ADVERSARIAL SCENARIOS PASSED")
    print("=" * 60)
