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

from sqlalchemy import func as sqlfunc
from sqlalchemy import select, update

from backend.models.agent import Agent
from backend.models.banking import CentralBank
from backend.models.transaction import Transaction
from backend.models.zone import Zone

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def process_survival_costs(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
    hours: int = 1,
    redis: aioredis.Redis | None = None,
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

    # Count active non-NPC agents (NPCs are funded differently, no survival cost)
    count_result = await db.execute(
        select(sqlfunc.count(Agent.id)).where(
            Agent.is_active == True,  # noqa: E712
            Agent.is_npc == False,  # noqa: E712
        )
    )
    agent_count = count_result.scalar_one()

    if agent_count == 0:
        return {
            "type": "survival_costs",
            "agents_charged": 0,
            "cost_per_agent": float(survival_cost),
            "total_deducted": 0.0,
        }

    # Batch UPDATE: deduct survival cost from all active non-NPC agents
    await db.execute(
        update(Agent)
        .where(
            Agent.is_active == True,  # noqa: E712
            Agent.is_npc == False,  # noqa: E712
        )
        .values(balance=Agent.balance - float(survival_cost))
    )

    # Bulk insert transactions — one per active non-NPC agent
    agent_ids_result = await db.execute(
        select(Agent.id).where(
            Agent.is_active == True,  # noqa: E712
            Agent.is_npc == False,  # noqa: E712
        )
    )
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

    # Emit food_charged events
    if redis is not None:
        from backend.events import emit_event

        for agent_id in agent_ids:
            await emit_event(
                redis,
                agent_id,
                "food_charged",
                {"amount": float(survival_cost), "hours": hours},
                clock,
            )

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
    clock: Clock,
    settings: Settings,
    hours: int = 1,
    redis: aioredis.Redis | None = None,
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

    # Check if any active non-NPC agents are housed
    housed_check = await db.execute(
        select(sqlfunc.count(Agent.id)).where(
            Agent.housing_zone_id.is_not(None),
            Agent.is_active == True,  # noqa: E712
            Agent.is_npc == False,  # noqa: E712
        )
    )
    housed_count = housed_check.scalar_one()
    if housed_count == 0:
        return {"type": "rent", "agents_charged": 0, "agents_evicted": 0, "total_collected": 0.0}

    # Load CentralBank — rent collected goes to bank reserves
    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1).with_for_update())
    central_bank = bank_result.scalar_one_or_none()

    charged_count = 0
    evicted_count = 0
    total_collected = Decimal("0")

    # Process rent per zone (each zone has a different rent_cost)
    for zone in zones:
        rent_due = Decimal(str(float(zone.rent_cost) * rent_modifier * hours))

        if rent_due <= 0:
            continue

        # Step 1: Find active non-NPC agents in this zone who CAN pay rent
        can_pay_result = await db.execute(
            select(Agent.id).where(
                Agent.housing_zone_id == zone.id,
                Agent.balance >= float(rent_due),
                Agent.is_active == True,  # noqa: E712
                Agent.is_npc == False,  # noqa: E712
            )
        )
        can_pay_ids = [row[0] for row in can_pay_result.all()]

        if can_pay_ids:
            # Batch deduct rent from all agents who can pay in this zone
            await db.execute(
                update(Agent).where(Agent.id.in_(can_pay_ids)).values(balance=Agent.balance - float(rent_due))
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

            # Emit rent_charged events
            if redis is not None:
                from backend.events import emit_event

                for agent_id in can_pay_ids:
                    await emit_event(
                        redis,
                        agent_id,
                        "rent_charged",
                        {"amount": float(rent_due), "zone": zone.slug, "hours": hours},
                        clock,
                    )

        # Step 2: Evict active non-NPC agents in this zone who CANNOT pay rent
        cant_pay_result = await db.execute(
            select(Agent.id, Agent.name).where(
                Agent.housing_zone_id == zone.id,
                Agent.balance < float(rent_due),
                Agent.is_active == True,  # noqa: E712
                Agent.is_npc == False,  # noqa: E712
            )
        )
        cant_pay = cant_pay_result.all()
        cant_pay_ids = [row[0] for row in cant_pay]

        if cant_pay_ids:
            await db.execute(update(Agent).where(Agent.id.in_(cant_pay_ids)).values(housing_zone_id=None))
            evicted_count += len(cant_pay_ids)

            for row in cant_pay:
                logger.info(
                    "Agent %s evicted from %s (couldn't afford rent: %.2f)",
                    row[1],
                    zone.name,
                    float(rent_due),
                )

            # Emit evicted events
            if redis is not None:
                from backend.events import emit_event

                for agent_id in cant_pay_ids:
                    await emit_event(
                        redis,
                        agent_id,
                        "evicted",
                        {"zone": zone.slug, "rent_due": float(rent_due)},
                        clock,
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


async def enforce_reserve_floor(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Ensure central bank reserves don't stay below the configured minimum.

    If reserves have fallen below ``min_bank_reserves``, the bank "prints
    money" (monetary injection) to top them back up.  This prevents the
    economy death spiral where NPC demand, loans, and GDP all freeze
    because reserves hit zero.

    Called once per slow tick, **after** rent / tax / loan collections
    have already replenished what they can organically.

    Returns:
        Dict with injection amount (0.0 if no injection was needed).
    """
    min_reserves = Decimal(str(settings.economy.min_bank_reserves))
    if min_reserves <= 0:
        return {"type": "reserve_floor", "injection": 0.0}

    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1).with_for_update())
    central_bank = bank_result.scalar_one_or_none()
    if central_bank is None:
        return {"type": "reserve_floor", "injection": 0.0}

    current = Decimal(str(central_bank.reserves))
    if current >= min_reserves:
        return {"type": "reserve_floor", "injection": 0.0}

    injection = min_reserves - current
    central_bank.reserves = min_reserves

    now = clock.now()
    txn = Transaction(
        type="monetary_injection",
        from_agent_id=None,
        to_agent_id=None,
        amount=float(injection),
        metadata_json={
            "reason": "reserve_floor",
            "previous_reserves": float(current),
            "new_reserves": float(min_reserves),
            "tick_time": now.isoformat(),
        },
    )
    db.add(txn)
    await db.flush()

    logger.info(
        "Reserve floor: injected %.2f (was %.2f, floor %.2f)",
        float(injection),
        float(current),
        float(min_reserves),
    )

    return {"type": "reserve_floor", "injection": float(injection)}
