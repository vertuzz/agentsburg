"""
Audit system for Agent Economy.

Randomly audits agents and fines/jails those who under-reported income.
Audits compare marketplace_income vs total_actual_income to find hidden income.
If discrepancy > threshold: fine + escalating jail time.

This creates the crime opportunity: agents who do most of their business via
direct trades report low marketplace income, pay little tax, but risk getting
caught in an audit. The riskiness is tunable via enforcement_probability in
the government template.

run_audits() — hourly (after tax collection), random selection
"""

from __future__ import annotations

import logging
import random
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.government import TaxRecord, Violation
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

# Fraction of total_actual_income that constitutes a "significant" discrepancy.
# Below this threshold, no audit action is taken (small rounding differences).
DISCREPANCY_THRESHOLD_FRACTION = 0.05


async def run_audits(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Randomly audit agents and fine/jail those who under-reported income.

    For each agent, roll random() against enforcement_probability. If selected:
    - Find their most recent unaudited TaxRecord
    - If discrepancy > DISCREPANCY_THRESHOLD_FRACTION * total_actual_income:
        - Calculate evaded_amount = discrepancy * tax_rate
        - Fine = evaded_amount * fine_multiplier (from government template)
        - If prior violations meet escalation threshold -> add jail time
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

        # Threshold: discrepancy must be > 5% of total income to be actionable
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
                jail_seconds = min(3600, max_jail_seconds)  # 1 hour
            elif prior_violations == escalation_threshold:
                jail_seconds = min(14400, max_jail_seconds)  # 4 hours
            else:
                jail_seconds = max_jail_seconds  # max (24h for authoritarian)

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
