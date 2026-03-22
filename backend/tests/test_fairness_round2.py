"""
Fairness Round 2 Tests

Verifies:
1. Jailed agents cannot call apply_job (IN_JAIL error)
2. Jailed agents cannot call set_prices (IN_JAIL error)
3. Agents with 3+ bankruptcies cannot take loans
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.agent import Agent
from tests.helpers import TestAgent, ToolCallError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def give_balance(app, agent_name: str, amount: float) -> None:
    """Directly set an agent's balance for test setup."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        agent.balance = Decimal(str(amount))
        await session.commit()


async def jail_agent(app, agent_name: str, clock, hours: float = 2.0) -> None:
    """Put an agent in jail for the given number of hours."""
    now = clock.now()
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        agent.jail_until = now + timedelta(hours=hours)
        await session.commit()


async def set_bankruptcy_count(app, agent_name: str, count: int) -> None:
    """Directly set an agent's bankruptcy_count for test setup."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        agent.bankruptcy_count = count
        await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_jailed_agent_cannot_apply_job(client, app, clock):
    """A jailed agent should be blocked from calling apply_job."""
    agent = await TestAgent.signup(client, "jailed_applicant")
    await give_balance(app, "jailed_applicant", 100)

    await jail_agent(app, "jailed_applicant", clock, hours=2.0)

    # apply_job requires a job_id; use a dummy UUID
    with pytest.raises(ToolCallError) as exc_info:
        await agent.call("apply_job", {"job_id": "00000000-0000-0000-0000-000000000000"})

    assert exc_info.value.code == "IN_JAIL"


@pytest.mark.asyncio
async def test_jailed_agent_cannot_set_prices(client, app, clock):
    """A jailed agent should be blocked from calling set_prices."""
    agent = await TestAgent.signup(client, "jailed_pricer")
    await give_balance(app, "jailed_pricer", 100)

    await jail_agent(app, "jailed_pricer", clock, hours=2.0)

    # set_prices requires business_id, product, price; use dummy values
    with pytest.raises(ToolCallError) as exc_info:
        await agent.call("set_prices", {
            "business_id": "00000000-0000-0000-0000-000000000000",
            "product": "bread",
            "price": 10.0,
        })

    assert exc_info.value.code == "IN_JAIL"


@pytest.mark.asyncio
async def test_bankruptcy_cycling_denied_loan(client, app, clock, db):
    """An agent with 3+ bankruptcies should be denied any loan."""
    agent = await TestAgent.signup(client, "serial_bankrupt")
    await give_balance(app, "serial_bankrupt", 1000)
    await set_bankruptcy_count(app, "serial_bankrupt", 3)

    # Attempt to take a loan — should be denied
    with pytest.raises(ToolCallError) as exc_info:
        await agent.call("bank", {"action": "take_loan", "amount": 50})

    assert "bankruptcies" in exc_info.value.message.lower() or "denied" in exc_info.value.message.lower()
