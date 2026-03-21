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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
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
) -> dict:
    """
    Deduct survival costs (food/living expenses) from all agents.

    Every hour, each agent's balance is reduced by survival_cost_per_hour.
    This is unavoidable — it represents food, basic utilities, etc.
    Agents with negative balances accumulate debt.

    Args:
        db:       Active async database session.
        clock:    Clock for transaction timestamps.
        settings: Application settings.

    Returns:
        Dict with count of agents charged and total amount deducted.
    """
    now = clock.now()
    survival_cost = Decimal(str(settings.economy.food_cost_per_day / 24))
    # food_cost_per_day / 24 = hourly food cost

    # Load all agents
    result = await db.execute(select(Agent))
    agents = list(result.scalars().all())

    total_deducted = Decimal("0")
    charged_count = 0

    for agent in agents:
        agent.balance = Decimal(str(agent.balance)) - survival_cost
        charged_count += 1
        total_deducted += survival_cost

        txn = Transaction(
            type="food",
            from_agent_id=agent.id,
            to_agent_id=None,  # consumed, leaves the economy
            amount=survival_cost,
            metadata_json={"tick_time": now.isoformat()},
        )
        db.add(txn)

    await db.flush()

    logger.info(
        "Survival costs: charged %d agents %.4f each (total: %.4f)",
        charged_count,
        float(survival_cost),
        float(total_deducted),
    )

    return {
        "type": "survival_costs",
        "agents_charged": charged_count,
        "cost_per_agent": float(survival_cost),
        "total_deducted": float(total_deducted),
    }


async def process_rent(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Deduct rent for all housed agents.

    Each housed agent pays their zone's rent_cost per hour.
    Agents who cannot afford rent are evicted (housing_zone_id set to None).

    Future: government template can modify rent via rent_modifier.

    Args:
        db:       Active async database session.
        clock:    Clock for transaction timestamps.
        settings: Application settings.

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

    # Load all housed agents with their zones in one query
    result = await db.execute(
        select(Agent).where(Agent.housing_zone_id.is_not(None))
    )
    housed_agents = list(result.scalars().all())

    if not housed_agents:
        return {"type": "rent", "agents_charged": 0, "agents_evicted": 0, "total_collected": 0.0}

    # Load zones referenced by housed agents
    zone_ids = {a.housing_zone_id for a in housed_agents}
    zones_result = await db.execute(select(Zone).where(Zone.id.in_(zone_ids)))
    zones_by_id = {z.id: z for z in zones_result.scalars().all()}

    charged_count = 0
    evicted_count = 0
    total_collected = Decimal("0")

    for agent in housed_agents:
        zone = zones_by_id.get(agent.housing_zone_id)
        if zone is None:
            # Zone no longer exists — evict
            agent.housing_zone_id = None
            evicted_count += 1
            continue

        rent_due = Decimal(str(float(zone.rent_cost) * rent_modifier))
        current_balance = Decimal(str(agent.balance))

        if current_balance >= rent_due:
            # Can afford rent
            agent.balance = current_balance - rent_due
            total_collected += rent_due
            charged_count += 1

            txn = Transaction(
                type="rent",
                from_agent_id=agent.id,
                to_agent_id=None,
                amount=rent_due,
                metadata_json={
                    "zone_slug": zone.slug,
                    "zone_name": zone.name,
                    "tick_time": now.isoformat(),
                },
            )
            db.add(txn)
        else:
            # Cannot afford rent — evict
            agent.housing_zone_id = None
            evicted_count += 1
            logger.info(
                "Agent %s evicted from %s (balance: %.2f, rent: %.2f)",
                agent.name,
                zone.name,
                float(current_balance),
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
