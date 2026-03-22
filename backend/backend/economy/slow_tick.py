"""
Slow tick processing for Agent Economy.

The slow tick runs hourly. It handles scheduled economic events:
- Survival cost deductions (food/living expenses)
- Rent deductions for housed agents
- Eviction of agents who can't afford rent

These costs are auto-deducted — agents don't need to take any action.
Failure to maintain a positive balance leads to debt and eventually bankruptcy.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update, func as sqlfunc, literal, cast, Numeric
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.banking import CentralBank
from backend.models.transaction import Transaction
from backend.models.zone import Zone

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def process_survival_costs(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
    hours: int = 1,
) -> dict:
    """
    Deduct survival costs (food/living expenses) from all agents.

    Every hour, each agent's balance is reduced by survival_cost_per_hour.
    This is unavoidable — it represents food, basic utilities, etc.
    Agents with negative balances accumulate debt.

    Uses a single batch UPDATE for all agents instead of per-agent locks.
    Within the tick's transaction, this is safe — no concurrent balance
    modifications can occur until the tick commits.

    Args:
        db:       Active async database session.
        clock:    Clock for transaction timestamps.
        settings: Application settings.
        hours:    Number of hours to charge for (default 1). Used when
                  catching up after a large time jump.

    Returns:
        Dict with count of agents charged and total amount deducted.
    """
    now = clock.now()
    survival_cost = Decimal(str(settings.economy.survival_cost_per_hour)) * hours

    # Count agents first (for return value)
    count_result = await db.execute(select(sqlfunc.count(Agent.id)))
    agent_count = count_result.scalar_one()

    if agent_count == 0:
        return {
            "type": "survival_costs",
            "agents_charged": 0,
            "cost_per_agent": float(survival_cost),
            "total_deducted": 0.0,
        }

    # Batch UPDATE: deduct survival cost from all agents in one query
    await db.execute(
        update(Agent).values(balance=Agent.balance - float(survival_cost))
    )

    # Bulk insert transactions — one per agent
    agent_ids_result = await db.execute(select(Agent.id))
    agent_ids = [row[0] for row in agent_ids_result.all()]

    for agent_id in agent_ids:
        txn = Transaction(
            type="food",
            from_agent_id=agent_id,
            to_agent_id=None,
            amount=survival_cost,
            metadata_json={"tick_time": now.isoformat()},
        )
        db.add(txn)

    await db.flush()

    total_deducted = survival_cost * agent_count

    logger.info(
        "Survival costs: charged %d agents %.4f each (total: %.4f)",
        agent_count,
        float(survival_cost),
        float(total_deducted),
    )

    return {
        "type": "survival_costs",
        "agents_charged": agent_count,
        "cost_per_agent": float(survival_cost),
        "total_deducted": float(total_deducted),
    }


async def process_rent(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
    hours: int = 1,
) -> dict:
    """
    Deduct rent for all housed agents.

    Each housed agent pays their zone's rent_cost per hour.
    Agents who cannot afford rent are evicted (housing_zone_id set to None).

    Uses batch queries: one UPDATE per zone for agents who can pay,
    then one UPDATE for evictions (agents who can't afford rent).

    Args:
        db:       Active async database session.
        clock:    Clock for transaction timestamps.
        settings: Application settings.
        hours:    Number of hours to charge for (default 1).

    Returns:
        Dict with payment counts and eviction counts.
    """
    now = clock.now()

    # Government rent modifier — reads from current GovernmentState (Phase 6)
    rent_modifier = 1.0
    try:
        from backend.government.service import get_current_policy
        policy = await get_current_policy(db, settings)
        rent_modifier = float(policy.get("rent_modifier", 1.0))
    except Exception:
        pass  # Fail gracefully if government tables don't exist yet

    # Pre-load zones (immutable config data)
    zones_result = await db.execute(select(Zone))
    zones = list(zones_result.scalars().all())
    zones_by_id = {z.id: z for z in zones}

    if not zones_by_id:
        return {"type": "rent", "agents_charged": 0, "agents_evicted": 0, "total_collected": 0.0}

    # Check if any agents are housed
    housed_check = await db.execute(
        select(sqlfunc.count(Agent.id)).where(Agent.housing_zone_id.is_not(None))
    )
    housed_count = housed_check.scalar_one()
    if housed_count == 0:
        return {"type": "rent", "agents_charged": 0, "agents_evicted": 0, "total_collected": 0.0}

    # Load CentralBank — rent collected goes to bank reserves
    bank_result = await db.execute(
        select(CentralBank).where(CentralBank.id == 1).with_for_update()
    )
    central_bank = bank_result.scalar_one_or_none()

    charged_count = 0
    evicted_count = 0
    total_collected = Decimal("0")

    # Process rent per zone (each zone has a different rent_cost)
    for zone in zones:
        rent_due = Decimal(str(float(zone.rent_cost) * rent_modifier * hours))

        if rent_due <= 0:
            continue

        # Step 1: Find agents in this zone who CAN pay rent
        # Use a batch UPDATE with a WHERE clause for balance >= rent_due
        can_pay_result = await db.execute(
            select(Agent.id).where(
                Agent.housing_zone_id == zone.id,
                Agent.balance >= float(rent_due),
            )
        )
        can_pay_ids = [row[0] for row in can_pay_result.all()]

        if can_pay_ids:
            # Batch deduct rent from all agents who can pay in this zone
            await db.execute(
                update(Agent)
                .where(Agent.id.in_(can_pay_ids))
                .values(balance=Agent.balance - float(rent_due))
            )

            zone_collected = rent_due * len(can_pay_ids)
            total_collected += zone_collected
            charged_count += len(can_pay_ids)

            # Credit bank reserves
            if central_bank is not None:
                central_bank.reserves = Decimal(str(central_bank.reserves)) + zone_collected

            # Create transactions for paying agents
            for agent_id in can_pay_ids:
                txn = Transaction(
                    type="rent",
                    from_agent_id=agent_id,
                    to_agent_id=None,
                    amount=rent_due,
                    metadata_json={
                        "zone_slug": zone.slug,
                        "zone_name": zone.name,
                        "tick_time": now.isoformat(),
                    },
                )
                db.add(txn)

        # Step 2: Evict agents in this zone who CANNOT pay rent
        cant_pay_result = await db.execute(
            select(Agent.id, Agent.name).where(
                Agent.housing_zone_id == zone.id,
                Agent.balance < float(rent_due),
            )
        )
        cant_pay = cant_pay_result.all()
        cant_pay_ids = [row[0] for row in cant_pay]

        if cant_pay_ids:
            await db.execute(
                update(Agent)
                .where(Agent.id.in_(cant_pay_ids))
                .values(housing_zone_id=None)
            )
            evicted_count += len(cant_pay_ids)

            for row in cant_pay:
                logger.info(
                    "Agent %s evicted from %s (couldn't afford rent: %.2f)",
                    row[1],
                    zone.name,
                    float(rent_due),
                )

    await db.flush()

    logger.info(
        "Rent processing: %d paid, %d evicted, %.2f total",
        charged_count,
        evicted_count,
        float(total_collected),
    )

    return {
        "type": "rent",
        "agents_charged": charged_count,
        "agents_evicted": evicted_count,
        "total_collected": float(total_collected),
    }
