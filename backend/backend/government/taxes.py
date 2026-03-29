"""
Tax collection system for Agent Economy.

The core mechanic:
  - Tax is assessed on "marketplace" income only (marketplace order fills,
    storefront NPC sales). This is what the tax authority can see.
  - Direct trades (type="trade") are NOT visible to the tax authority.
  - Audits (see auditing.py) compare marketplace_income vs total_actual_income
    to find hidden income.
  - If discrepancy > threshold: fine + escalating jail time.

This creates the crime opportunity: agents who do most of their business via
direct trades report low marketplace income, pay little tax, but risk getting
caught in an audit. The riskiness is tunable via enforcement_probability in
the government template.

collect_taxes() — hourly, for all agents
run_audits()    — re-exported from backend.government.auditing
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

# Re-export run_audits so existing imports from taxes.py still work
from backend.government.auditing import run_audits  # noqa: F401
from backend.models.agent import Agent
from backend.models.government import TaxRecord
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

# Transaction types that count as "marketplace" income (visible to tax authority)
MARKETPLACE_INCOME_TYPES = frozenset({"marketplace", "storefront"})

# Transaction types that count as general income (ALL income, including off-book)
# Audits should catch untaxed direct trades — NOT wages or gathering, which are
# legitimate game mechanics that agents have no way to voluntarily tax.
# Including wages here punishes agents simply for being employed, with no recourse.
TOTAL_INCOME_TYPES = frozenset({"marketplace", "storefront", "trade"})


async def collect_taxes(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Collect taxes from all agents for the current period.

    For each agent:
    1. Sum their "marketplace" type transactions since the last tax period
       (these are what the tax authority officially sees)
    2. Sum ALL income-generating transactions (including direct trades)
    3. Calculate tax_owed = marketplace_income * tax_rate
    4. Deduct from agent balance (collect what we can, even if insufficient)
    5. Add collected tax to CentralBank.reserves
    6. Create TaxRecord and Transaction(type="tax")

    The period lookback is tax_audit_period_seconds (default 1 hour).

    Args:
        db:       Active async database session.
        clock:    Clock for current time and period calculation.
        settings: Application settings.

    Returns:
        Summary dict with counts and totals.
    """
    from backend.government.service import get_current_policy

    now = clock.now()
    policy = await get_current_policy(db, settings)
    tax_rate = Decimal(str(policy.get("tax_rate", 0.05)))

    # Look-back window: 1 hour (tax_audit_period_seconds)
    audit_period = getattr(settings.economy, "tax_audit_period_seconds", 3600)
    from datetime import timedelta

    period_start = now - timedelta(seconds=audit_period)

    # Get CentralBank for collecting reserves
    central_bank = None
    try:
        from backend.models.banking import CentralBank

        bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1))
        central_bank = bank_result.scalar_one_or_none()
    except ImportError:
        pass

    # Load all active agents — skip deactivated
    agents_result = await db.execute(
        select(Agent).where(Agent.is_active == True)  # noqa: E712
    )
    agents = list(agents_result.scalars().all())

    # --- Batch income summation (2 queries instead of 2*N) ---
    # Pre-compute marketplace income and total income for ALL agents at once
    marketplace_income_map = await _batch_sum_income(db, MARKETPLACE_INCOME_TYPES, period_start, now)
    total_income_map = await _batch_sum_income(db, TOTAL_INCOME_TYPES, period_start, now)

    total_tax_collected = Decimal("0")
    records_created = 0

    for agent in agents:
        marketplace_income = marketplace_income_map.get(agent.id, Decimal("0"))
        total_actual_income = total_income_map.get(agent.id, Decimal("0"))

        discrepancy = max(Decimal("0"), total_actual_income - marketplace_income)

        # Tax is only assessed on marketplace income (what authority can see)
        tax_owed = marketplace_income * tax_rate

        if tax_owed <= Decimal("0"):
            # Create a zero-tax record for audit purposes (to have a full picture)
            tax_record = TaxRecord(
                agent_id=agent.id,
                period_start=period_start,
                period_end=now,
                marketplace_income=float(marketplace_income),
                total_actual_income=float(total_actual_income),
                tax_owed=0.0,
                tax_paid=0.0,
                discrepancy=float(discrepancy),
                audited=False,
            )
            db.add(tax_record)
            records_created += 1
            continue

        # Collect what we can
        current_balance = Decimal(str(agent.balance))
        tax_paid = min(tax_owed, max(Decimal("0"), current_balance))

        agent.balance = current_balance - tax_paid
        total_tax_collected += tax_paid

        # Add to bank reserves
        if central_bank is not None and tax_paid > 0:
            central_bank.reserves = Decimal(str(central_bank.reserves)) + tax_paid

        # Record the tax transaction
        if tax_paid > 0:
            txn = Transaction(
                type="tax",
                from_agent_id=agent.id,
                to_agent_id=None,  # goes to bank reserves
                amount=float(tax_paid),
                metadata_json={
                    "tax_rate": float(tax_rate),
                    "marketplace_income": float(marketplace_income),
                    "period_start": period_start.isoformat(),
                    "period_end": now.isoformat(),
                },
            )
            db.add(txn)

        # Create TaxRecord
        tax_record = TaxRecord(
            agent_id=agent.id,
            period_start=period_start,
            period_end=now,
            marketplace_income=float(marketplace_income),
            total_actual_income=float(total_actual_income),
            tax_owed=float(tax_owed),
            tax_paid=float(tax_paid),
            discrepancy=float(discrepancy),
            audited=False,
        )
        db.add(tax_record)
        records_created += 1

    await db.flush()

    logger.info(
        "Tax collection: %d agents, %.2f total collected (rate: %.2f%%)",
        len(agents),
        float(total_tax_collected),
        float(tax_rate) * 100,
    )

    return {
        "type": "tax_collection",
        "agents_processed": len(agents),
        "records_created": records_created,
        "total_collected": float(total_tax_collected),
        "tax_rate": float(tax_rate),
    }


async def _sum_agent_income(
    db: AsyncSession,
    agent_id,
    income_types: frozenset,
    period_start,
    period_end,
) -> Decimal:
    """
    Sum transactions where the agent is the recipient (to_agent_id == agent_id)
    and the transaction type is in income_types, within the given period.
    """
    from sqlalchemy import func as sqlfunc

    result = await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(Transaction.amount), 0)).where(
            Transaction.to_agent_id == agent_id,
            Transaction.type.in_(list(income_types)),
            Transaction.created_at >= period_start,
            Transaction.created_at <= period_end,
        )
    )
    val = result.scalar_one()
    return Decimal(str(val)) if val else Decimal("0")


async def _batch_sum_income(
    db: AsyncSession,
    income_types: frozenset,
    period_start,
    period_end,
) -> dict:
    """
    Sum income for ALL agents in a single GROUP BY query.

    Returns a dict mapping agent_id -> Decimal income total.
    Agents with no income in the period are not included (default to 0).
    """
    from sqlalchemy import func as sqlfunc

    result = await db.execute(
        select(
            Transaction.to_agent_id,
            sqlfunc.coalesce(sqlfunc.sum(Transaction.amount), 0),
        )
        .where(
            Transaction.to_agent_id.is_not(None),
            Transaction.type.in_(list(income_types)),
            Transaction.created_at >= period_start,
            Transaction.created_at <= period_end,
        )
        .group_by(Transaction.to_agent_id)
    )
    return {row[0]: Decimal(str(row[1])) if row[1] else Decimal("0") for row in result.all()}
