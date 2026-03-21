"""
Housing domain logic for Agent Economy.

Agents rent housing in zones via rent_housing(). This:
1. Finds the target zone
2. Checks affordability (first rent payment + some buffer)
3. Sets agent.housing_zone_id
4. Deducts first rent payment from balance
5. Creates a transaction record

Rent is then deducted automatically on each slow tick (hourly).
Agents can relocate but pay a relocation fee.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.transaction import Transaction
from backend.models.zone import Zone

if TYPE_CHECKING:
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def rent_housing(
    db: AsyncSession,
    agent: Agent,
    zone_slug: str,
    settings: "Settings",
) -> dict:
    """
    Rent housing for an agent in the specified zone.

    The agent pays the first hour's rent immediately. Subsequent rent
    is deducted automatically on each slow tick.

    Agents can re-call this to move to a different zone. Moving costs a
    relocation fee on top of the first rent payment.

    Args:
        db:        Active async database session.
        agent:     The agent renting housing.
        zone_slug: Slug of the zone to move to.
        settings:  Application settings.

    Returns:
        Dict with zone info and new balance.

    Raises:
        ValueError: If the zone doesn't exist or the agent can't afford it.
    """
    # Find the zone
    result = await db.execute(select(Zone).where(Zone.slug == zone_slug))
    zone = result.scalar_one_or_none()
    if zone is None:
        raise ValueError(f"Zone not found: {zone_slug!r}")

    rent_cost = float(zone.rent_cost)

    # Calculate total cost (first rent + relocation if moving)
    is_relocation = (
        agent.housing_zone_id is not None
        and agent.housing_zone_id != zone.id
    )
    relocation_fee = settings.economy.relocation_cost if is_relocation else 0.0
    total_cost = rent_cost + relocation_fee

    current_balance = float(agent.balance)
    if current_balance < total_cost:
        raise ValueError(
            f"Insufficient funds to rent in {zone.name}. "
            f"Need {total_cost:.2f} (rent: {rent_cost:.2f}"
            + (f", relocation: {relocation_fee:.2f}" if is_relocation else "")
            + f"), have {current_balance:.2f}"
        )

    # Deduct first rent payment
    agent.balance = round(current_balance - total_cost, 2)
    agent.housing_zone_id = zone.id

    # Record rent transaction
    txn = Transaction(
        type="rent",
        from_agent_id=agent.id,
        to_agent_id=None,  # goes to bank/system
        amount=rent_cost,
        metadata_json={"zone_slug": zone_slug, "zone_name": zone.name},
    )
    db.add(txn)

    # Record relocation fee if applicable
    if relocation_fee > 0:
        rel_txn = Transaction(
            type="rent",
            from_agent_id=agent.id,
            to_agent_id=None,
            amount=relocation_fee,
            metadata_json={"zone_slug": zone_slug, "type": "relocation_fee"},
        )
        db.add(rel_txn)

    await db.flush()

    logger.info(
        "Agent %s rented housing in %s (cost: %.2f, balance: %.2f)",
        agent.name,
        zone.name,
        total_cost,
        float(agent.balance),
    )

    return {
        "zone_slug": zone.slug,
        "zone_name": zone.name,
        "rent_cost_per_hour": rent_cost,
        "first_payment": rent_cost,
        "relocation_fee": relocation_fee,
        "new_balance": float(agent.balance),
    }
