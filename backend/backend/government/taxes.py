"""
Tax collection and audit system for Agent Economy.

The core mechanic:
  - Tax is assessed on "marketplace" income only (marketplace order fills,
    storefront NPC sales). This is what the tax authority can see.
  - Direct trades (type="trade") are NOT visible to the tax authority.
  - Audits compare marketplace_income vs total_actual_income to find hidden income.
  - If discrepancy > threshold → fine + escalating jail time.

This creates the crime opportunity: agents who do most of their business via
direct trades report low marketplace income, pay little tax, but risk getting
caught in an audit. The riskiness is tunable via enforcement_probability in
the government template.

collect_taxes() — hourly, for all agents
run_audits()    — hourly (after tax collection), random selection
"""

from __future__ import annotations

import logging
import random
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.government import Violation, TaxRecord
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

# Fraction of total_actual_income that constitutes a "significant" discrepancy.
# Below this threshold, no audit action is taken (small rounding differences).
DISCREPANCY_THRESHOLD_FRACTION = 0.05

# Transaction types that count as "marketplace" income (visible to tax authority)
MARKETPLACE_INCOME_TYPES = frozenset({"marketplace", "storefront"})

# Transaction types that count as general income (ALL income, including off-book)
# We include: marketplace fills, storefront sales, direct trades, wages, gathering sales,
# and deposit interest — all income streams visible to the audit system
TOTAL_INCOME_TYPES = frozenset({"marketplace", "storefront", "trade", "wage", "gathering", "deposit_interest"})


async def collect_taxes(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
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
    marketplace_income_map = await _batch_sum_income(
        db, MARKETPLACE_INCOME_TYPES, period_start, now
    )
    total_income_map = await _batch_sum_income(
        db, TOTAL_INCOME_TYPES, period_start, now
    )

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


async def run_audits(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Randomly audit agents and fine/jail those who under-reported income.

    For each agent, roll random() against enforcement_probability. If selected:
    - Find their most recent unaudited TaxRecord
    - If discrepancy > DISCREPANCY_THRESHOLD_FRACTION * total_actual_income:
        - Calculate evaded_amount = discrepancy * tax_rate
        - Fine = evaded_amount * fine_multiplier (from government template)
        - If prior violations meet escalation threshold → add jail time
        - Create Violation record
        - Deduct fine from balance
        - Set agent.jail_until if jailed
        - Increment agent.violation_count
        - Create Transaction(type="fine")
    - Mark TaxRecord.audited = True

    Args:
        db:       Active async database session.
        clock:    Clock for current time.
        settings: Application settings.

    Returns:
        Summary dict with audit counts and violations.
    """
    from backend.government.service import get_current_policy

    now = clock.now()
    policy = await get_current_policy(db, settings)
    enforcement_prob = float(policy.get("enforcement_probability", 0.10))
    tax_rate = Decimal(str(policy.get("tax_rate", 0.05)))
    fine_multiplier = Decimal(str(policy.get("fine_multiplier", 1.5)))
    max_jail_seconds = int(policy.get("max_jail_seconds", 3600))

    # Escalation thresholds from economy.yaml (default: 3rd violation = jail)
    escalation_threshold = getattr(settings.economy, "violation_escalation_threshold", 3)

    # CentralBank to collect fines into reserves
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

    audited_count = 0
    violations_count = 0
    jailed_count = 0
    total_fines = Decimal("0")

    for agent in agents:
        # Random roll
        if random.random() >= enforcement_prob:
            continue

        # Find the most recent unaudited tax record for this agent
        record_result = await db.execute(
            select(TaxRecord)
            .where(
                TaxRecord.agent_id == agent.id,
                TaxRecord.audited == False,  # noqa: E712
            )
            .order_by(TaxRecord.period_end.desc())
            .limit(1)
        )
        tax_record = record_result.scalar_one_or_none()

        if tax_record is None:
            # Nothing to audit — agent has no pending tax records
            continue

        # Mark as audited regardless of outcome
        tax_record.audited = True
        audited_count += 1

        # Check if discrepancy is significant
        total_income = Decimal(str(tax_record.total_actual_income))
        discrepancy = Decimal(str(tax_record.discrepancy))

        if total_income <= 0 or discrepancy <= 0:
            continue

        # Threshold: discrepancy must be > 10% of total income to be actionable
        threshold = total_income * Decimal(str(DISCREPANCY_THRESHOLD_FRACTION))
        if discrepancy < threshold:
            continue

        # Significant discrepancy found — calculate fine
        evaded_tax = discrepancy * tax_rate
        fine_amount = evaded_tax * fine_multiplier

        # Determine jail time based on violation count (BEFORE incrementing)
        prior_violations = agent.violation_count
        jail_until = None

        if prior_violations >= escalation_threshold - 1:  # 3rd+ offense
            # Escalating jail: 1h, 4h, 24h based on total violation count
            if prior_violations == escalation_threshold - 1:
                jail_seconds = min(3600, max_jail_seconds)   # 1 hour
            elif prior_violations == escalation_threshold:
                jail_seconds = min(14400, max_jail_seconds)  # 4 hours
            else:
                jail_seconds = max_jail_seconds               # max (24h for authoritarian)

            from datetime import timedelta
            # Extend existing jail if already jailed
            base_time = max(now, agent.jail_until) if agent.jail_until else now
            jail_until = base_time + timedelta(seconds=jail_seconds)
            agent.jail_until = jail_until
            jailed_count += 1

        # Deduct fine from agent balance
        current_balance = Decimal(str(agent.balance))
        actual_fine = min(fine_amount, max(Decimal("0"), current_balance))
        # We still record the full fine_amount even if they can't fully pay —
        # remaining debt pushes them toward bankruptcy
        agent.balance = current_balance - fine_amount

        # Add fine to bank reserves
        if central_bank is not None and actual_fine > 0:
            central_bank.reserves = Decimal(str(central_bank.reserves)) + actual_fine

        total_fines += fine_amount

        # Increment violation count
        agent.violation_count += 1
        violations_count += 1

        # Create Violation record
        violation = Violation(
            agent_id=agent.id,
            type="tax_evasion",
            amount_evaded=float(discrepancy),
            fine_amount=float(fine_amount),
            jail_until=jail_until,
            detected_at=now,
        )
        db.add(violation)

        # Create fine Transaction
        if fine_amount > 0:
            txn = Transaction(
                type="fine",
                from_agent_id=agent.id,
                to_agent_id=None,
                amount=float(fine_amount),
                metadata_json={
                    "violation_type": "tax_evasion",
                    "discrepancy": float(discrepancy),
                    "evaded_tax": float(evaded_tax),
                    "fine_multiplier": float(fine_multiplier),
                    "jail_until": jail_until.isoformat() if jail_until else None,
                    "prior_violations": prior_violations,
                },
            )
            db.add(txn)

        logger.info(
            "Audit violation: agent=%s discrepancy=%.2f evaded_tax=%.2f fine=%.2f jailed=%s",
            agent.name,
            float(discrepancy),
            float(evaded_tax),
            float(fine_amount),
            jail_until is not None,
        )

    await db.flush()

    logger.info(
        "Audits complete: %d audited, %d violations, %d jailed, %.2f total fines",
        audited_count,
        violations_count,
        jailed_count,
        float(total_fines),
    )

    return {
        "type": "audits",
        "enforcement_probability": enforcement_prob,
        "agents_audited": audited_count,
        "violations_found": violations_count,
        "jailed": jailed_count,
        "total_fines": float(total_fines),
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
        select(sqlfunc.coalesce(sqlfunc.sum(Transaction.amount), 0))
        .where(
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
    return {
        row[0]: Decimal(str(row[1])) if row[1] else Decimal("0")
        for row in result.all()
    }
