"""
Tax Fairness and Jail Enforcement Tests

Verifies:
1. Expanded audit visibility: gathering, wage, and trade income are visible
   to the audit discrepancy calculation (but not directly taxed).
2. Marketplace/storefront income is still taxed normally (no regression).
3. Jailed agents cannot call gather or work.
4. Wage income contributes to audit discrepancy.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.transaction import Transaction
from backend.government.taxes import (
    MARKETPLACE_INCOME_TYPES,
    TOTAL_INCOME_TYPES,
    collect_taxes,
)
from tests.conftest import give_balance, jail_agent
from tests.helpers import TestAgent, ToolCallError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def create_income_transaction(
    app, agent_name: str, txn_type: str, amount: float, clock
) -> None:
    """Create a transaction of the given type credited to the agent."""
    now = clock.now()
    async with app.state.session_factory() as session:
        result = await session.execute(select(Agent).where(Agent.name == agent_name))
        agent = result.scalar_one()
        txn = Transaction(
            type=txn_type,
            from_agent_id=None,
            to_agent_id=agent.id,
            amount=amount,
            created_at=now,
            metadata_json={"test": True},
        )
        session.add(txn)
        await session.commit()


# ---------------------------------------------------------------------------
# 1. TOTAL_INCOME_TYPES includes new income streams
# ---------------------------------------------------------------------------

def test_total_income_types_includes_wage_gathering_interest():
    """Verify that TOTAL_INCOME_TYPES now includes wage, gathering, deposit_interest."""
    assert "wage" in TOTAL_INCOME_TYPES
    assert "gathering" in TOTAL_INCOME_TYPES
    assert "deposit_interest" in TOTAL_INCOME_TYPES
    # Still includes the original types
    assert "marketplace" in TOTAL_INCOME_TYPES
    assert "storefront" in TOTAL_INCOME_TYPES
    assert "trade" in TOTAL_INCOME_TYPES


def test_marketplace_income_types_unchanged():
    """Marketplace income types should NOT include wage/gathering/interest."""
    assert "wage" not in MARKETPLACE_INCOME_TYPES
    assert "gathering" not in MARKETPLACE_INCOME_TYPES
    assert "deposit_interest" not in MARKETPLACE_INCOME_TYPES
    # Should only have marketplace and storefront
    assert MARKETPLACE_INCOME_TYPES == frozenset({"marketplace", "storefront"})


# ---------------------------------------------------------------------------
# 2. Agent earning only through gathering/trades gets audit discrepancy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gathering_income_creates_audit_discrepancy(
    client, app, clock, run_tick, db, redis_client
):
    """
    An agent who earns only through gathering (no marketplace income) should
    have a discrepancy detected by the audit system, because gathering income
    is now in TOTAL_INCOME_TYPES.
    """
    agent = await TestAgent.signup(client, "gatherer_evader")
    await give_balance(app, "gatherer_evader", 100)

    # Create gathering income transactions (simulating sold gathered goods via trade)
    await create_income_transaction(app, "gatherer_evader", "gathering", 500.0, clock)
    await create_income_transaction(app, "gatherer_evader", "trade", 300.0, clock)

    # Run tax collection — should see 0 marketplace income but 800 total
    async with app.state.session_factory() as session:
        result = await collect_taxes(session, clock, app.state.settings)
        await session.commit()

    # Check tax records — the agent should have a discrepancy
    from backend.models.government import TaxRecord
    async with app.state.session_factory() as session:
        records = await session.execute(
            select(TaxRecord).where(TaxRecord.agent_id != None)
        )
        all_records = list(records.scalars().all())
        evader_records = [
            r for r in all_records
            if r.marketplace_income == 0.0 and r.total_actual_income > 0
        ]
        assert len(evader_records) >= 1, "Should have a tax record with discrepancy"
        record = evader_records[0]
        assert record.total_actual_income == 800.0
        assert record.marketplace_income == 0.0
        assert record.discrepancy == 800.0


@pytest.mark.asyncio
async def test_wage_income_visible_to_audit(
    client, app, clock, run_tick, db, redis_client
):
    """
    Wage income should now be included in total_actual_income for audit purposes.
    An agent with only wage income should have a discrepancy.
    """
    agent = await TestAgent.signup(client, "wage_only")
    await give_balance(app, "wage_only", 50)

    # Create wage income
    await create_income_transaction(app, "wage_only", "wage", 1000.0, clock)

    # Run tax collection
    async with app.state.session_factory() as session:
        result = await collect_taxes(session, clock, app.state.settings)
        await session.commit()

    # Check the tax record
    from backend.models.government import TaxRecord
    async with app.state.session_factory() as session:
        records = await session.execute(
            select(TaxRecord).where(TaxRecord.agent_id != None)
        )
        all_records = list(records.scalars().all())
        wage_records = [
            r for r in all_records
            if r.total_actual_income >= 1000.0 and r.marketplace_income == 0.0
        ]
        assert len(wage_records) >= 1, "Should have record with wage income discrepancy"
        record = wage_records[0]
        assert record.discrepancy >= 1000.0


# ---------------------------------------------------------------------------
# 3. Marketplace income still taxed normally (no regression)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_marketplace_income_still_taxed(
    client, app, clock, run_tick, db, redis_client
):
    """
    An agent with marketplace income should still be taxed on it.
    This is a regression test: expanding TOTAL_INCOME_TYPES must not change
    how marketplace income is taxed.
    """
    agent = await TestAgent.signup(client, "tax_mkt_seller")
    await give_balance(app, "tax_mkt_seller", 500)

    # Create marketplace income
    await create_income_transaction(app, "tax_mkt_seller", "marketplace", 200.0, clock)

    # Run tax collection
    async with app.state.session_factory() as session:
        result = await collect_taxes(session, clock, app.state.settings)
        await session.commit()

    # Agent should have had tax deducted
    from backend.models.government import TaxRecord
    async with app.state.session_factory() as session:
        records = await session.execute(
            select(TaxRecord).where(TaxRecord.agent_id != None)
        )
        all_records = list(records.scalars().all())
        seller_records = [
            r for r in all_records
            if r.marketplace_income >= 200.0 and r.tax_owed > 0
        ]
        assert len(seller_records) >= 1, "Marketplace income should generate tax"
        record = seller_records[0]
        assert record.tax_owed > 0
        assert record.tax_paid > 0


# ---------------------------------------------------------------------------
# 4. Jailed agent cannot gather
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_jailed_agent_cannot_gather(
    client, app, clock, run_tick, db, redis_client
):
    """A jailed agent should be blocked from calling gather."""
    agent = await TestAgent.signup(client, "jailed_gatherer")
    await give_balance(app, "jailed_gatherer", 100)

    # Jail the agent
    await jail_agent(app, "jailed_gatherer", clock, hours=2.0)

    # Try to gather — should fail with IN_JAIL
    with pytest.raises(ToolCallError) as exc_info:
        await agent.call("gather", {"resource": "berries"})

    assert exc_info.value.code == "IN_JAIL"


# ---------------------------------------------------------------------------
# 5. Jailed agent cannot work
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_jailed_agent_cannot_work(
    client, app, clock, run_tick, db, redis_client
):
    """A jailed agent should be blocked from calling work."""
    agent = await TestAgent.signup(client, "jailed_worker")
    await give_balance(app, "jailed_worker", 100)

    # Jail the agent
    await jail_agent(app, "jailed_worker", clock, hours=2.0)

    # Try to work — should fail with IN_JAIL
    with pytest.raises(ToolCallError) as exc_info:
        await agent.call("work", {})

    assert exc_info.value.code == "IN_JAIL"
