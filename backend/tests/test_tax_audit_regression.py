from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from backend.government.auditing import run_audits
from backend.government.service import get_policy_params
from backend.government.taxes import collect_taxes
from backend.models.agent import Agent
from backend.models.government import TaxRecord, Violation
from backend.models.transaction import Transaction


async def _create_agent(session, name: str, *, balance: str, is_active: bool = True) -> Agent:
    agent = Agent(
        name=name,
        action_token=f"{name}-action",
        view_token=f"{name}-view",
        model="test-model",
        balance=Decimal(balance),
        is_active=is_active,
    )
    session.add(agent)
    await session.flush()
    return agent


def _income_txn(
    *,
    txn_type: str,
    from_agent_id,
    to_agent_id,
    amount: str,
    created_at,
) -> Transaction:
    return Transaction(
        type=txn_type,
        from_agent_id=from_agent_id,
        to_agent_id=to_agent_id,
        amount=Decimal(amount),
        created_at=created_at,
        updated_at=created_at,
    )


async def test_wage_income_does_not_create_audit_discrepancy(app, clock, monkeypatch):
    now = clock.now()

    async with app.state.session_factory() as session:
        worker = await _create_agent(session, "audit_worker", balance="100.00")
        employer = await _create_agent(session, "audit_employer", balance="100.00", is_active=False)
        session.add(
            _income_txn(
                txn_type="wage",
                from_agent_id=employer.id,
                to_agent_id=worker.id,
                amount="100.00",
                created_at=now,
            )
        )
        await session.commit()
        worker_id = worker.id

    async with app.state.session_factory() as session:
        summary = await collect_taxes(session, clock, app.state.settings)
        await session.commit()
        tax_record = (
            await session.execute(select(TaxRecord).where(TaxRecord.agent_id == worker_id))
        ).scalar_one()

    monkeypatch.setattr("backend.government.auditing.random.random", lambda: 0.0)

    async with app.state.session_factory() as session:
        await run_audits(session, clock, app.state.settings)
        await session.commit()
        violations = (
            await session.execute(select(Violation).where(Violation.agent_id == worker_id))
        ).scalars().all()

    assert summary["records_created"] >= 1
    assert Decimal(str(tax_record.marketplace_income)) == Decimal("0")
    assert Decimal(str(tax_record.total_actual_income)) == Decimal("0")
    assert Decimal(str(tax_record.discrepancy)) == Decimal("0")
    assert violations == []


async def test_direct_trade_income_still_triggers_tax_evasion_audit(app, clock, monkeypatch):
    now = clock.now()
    policy = get_policy_params(app.state.settings, "free_market")
    expected_fine = (
        Decimal("100.00")
        * Decimal(str(policy["tax_rate"]))
        * Decimal(str(policy["fine_multiplier"]))
    )

    async with app.state.session_factory() as session:
        trader = await _create_agent(session, "audit_trader", balance="100.00")
        counterparty = await _create_agent(session, "audit_counterparty", balance="100.00", is_active=False)
        session.add(
            _income_txn(
                txn_type="trade",
                from_agent_id=counterparty.id,
                to_agent_id=trader.id,
                amount="100.00",
                created_at=now,
            )
        )
        await session.commit()
        trader_id = trader.id

    async with app.state.session_factory() as session:
        summary = await collect_taxes(session, clock, app.state.settings)
        await session.commit()
        tax_record = (
            await session.execute(select(TaxRecord).where(TaxRecord.agent_id == trader_id))
        ).scalar_one()

    monkeypatch.setattr("backend.government.auditing.random.random", lambda: 0.0)

    async with app.state.session_factory() as session:
        await run_audits(session, clock, app.state.settings)
        await session.commit()
        violation = (
            await session.execute(select(Violation).where(Violation.agent_id == trader_id))
        ).scalar_one()
        trader = (await session.execute(select(Agent).where(Agent.id == trader_id))).scalar_one()

    assert summary["records_created"] >= 1
    assert Decimal(str(tax_record.marketplace_income)) == Decimal("0")
    assert Decimal(str(tax_record.total_actual_income)) == Decimal("100.00")
    assert Decimal(str(tax_record.discrepancy)) == Decimal("100.00")
    assert Decimal(str(violation.amount_evaded)) == Decimal("100.00")
    assert Decimal(str(violation.fine_amount)) == expected_fine
    assert trader.violation_count == 1
