"""
Business registration and closure for Agent Economy.

Handles:
  - register_business: create a new business (requires housing, costs money)
  - close_business: close a business and terminate all employees
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.business import Business, Employment, JobPosting
from backend.models.transaction import Transaction
from backend.models.zone import Zone

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def register_business(
    db: AsyncSession,
    agent: Agent,
    name: str,
    type_slug: str,
    zone_slug: str,
    settings: Settings,
    clock: Clock,
) -> dict:
    """
    Register a new business in the economy.

    Requirements:
    - Agent must have housing (housing_zone_id set)
    - Agent must be able to afford the registration cost
    - Zone must exist and allow the business type

    Args:
        db:        Active async database session.
        agent:     The registering agent (becomes owner).
        name:      Display name for the business.
        type_slug: Business type (e.g., "bakery", "smithy").
        zone_slug: Zone slug where the business will operate.
        settings:  Application settings.
        clock:     Clock for timestamps.

    Returns:
        Dict with business details and updated agent balance.

    Raises:
        ValueError: If validation fails (homeless, can't afford, zone invalid).
    """
    now = clock.now()

    # Must have housing to register a business
    if agent.is_homeless():
        raise ValueError("You must have housing before registering a business. Call rent_housing(zone) first.")

    # Look up zone
    result = await db.execute(select(Zone).where(Zone.slug == zone_slug))
    zone = result.scalar_one_or_none()
    if zone is None:
        raise ValueError(f"Zone not found: {zone_slug!r}")

    # Check zone allows this business type
    if zone.allowed_business_types is not None and type_slug not in zone.allowed_business_types:
        raise ValueError(
            f"Zone {zone.name!r} does not allow business type {type_slug!r}. "
            f"Allowed types: {zone.allowed_business_types}"
        )

    # Lock agent row to prevent concurrent balance manipulation
    agent_row = await db.execute(select(Agent).where(Agent.id == agent.id).with_for_update())
    agent = agent_row.scalar_one()

    # Check registration cost (modified by current government licensing policy)
    base_reg_cost = float(settings.economy.business_registration_cost)
    try:
        from backend.government.service import get_current_policy

        policy = await get_current_policy(db, settings)
        licensing_modifier = float(policy.get("licensing_cost_modifier", 1.0))
    except Exception:
        licensing_modifier = 1.0
    reg_cost = Decimal(str(base_reg_cost * licensing_modifier))
    agent_balance = Decimal(str(agent.balance))

    if agent_balance < reg_cost:
        raise ValueError(
            f"Insufficient funds to register a business. "
            f"Need {float(reg_cost):.2f}, have {float(agent_balance):.2f}. "
            f"Keep gathering or working to earn more."
        )

    # Deduct registration fee
    agent.balance = agent_balance - reg_cost

    # Create the business
    business = Business(
        owner_id=agent.id,
        name=name,
        type_slug=type_slug,
        zone_id=zone.id,
        storage_capacity=settings.economy.business_storage_capacity,
        is_npc=False,
    )
    db.add(business)

    # Record transaction
    txn = Transaction(
        type="business_reg",
        from_agent_id=agent.id,
        to_agent_id=None,
        amount=reg_cost,
        metadata_json={
            "business_name": name,
            "type_slug": type_slug,
            "zone_slug": zone_slug,
            "timestamp": now.isoformat(),
        },
    )
    db.add(txn)

    await db.flush()

    logger.info(
        "Agent %s registered business %r (type=%s, zone=%s, cost=%.2f)",
        agent.name,
        name,
        type_slug,
        zone_slug,
        float(reg_cost),
    )

    return {
        "business_id": str(business.id),
        "name": business.name,
        "type_slug": business.type_slug,
        "zone_slug": zone.slug,
        "zone_name": zone.name,
        "registration_cost": float(reg_cost),
        "new_balance": float(agent.balance),
        "storage_capacity": business.storage_capacity,
        "_hints": {
            "next_steps": [
                "Call configure_production(business_id, product) to set what to produce",
                "Call set_prices(business_id, product, price) to set storefront prices",
                "Call manage_employees(business_id, action='post_job', ...) to hire workers",
                "Call work() to produce goods yourself",
            ]
        },
    }


async def close_business(
    db: AsyncSession,
    agent: Agent,
    business_id: uuid.UUID,
    clock: Clock,
) -> dict:
    """
    Close a business, terminating all active employees.

    Only the business owner can close it.

    Args:
        db:          Active async database session.
        agent:       The requesting agent (must be owner).
        business_id: UUID of the business to close.
        clock:       Clock for timestamps.

    Returns:
        Dict with closure confirmation and terminated employee count.

    Raises:
        ValueError: If business not found, agent is not the owner, or already closed.
    """
    now = clock.now()

    # Look up business
    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()

    if business is None:
        raise ValueError(f"Business not found: {business_id}")

    if business.owner_id != agent.id:
        raise ValueError("You can only close your own businesses.")

    if not business.is_open():
        raise ValueError(f"Business {business.name!r} is already closed.")

    # Terminate all active employees
    emp_result = await db.execute(
        select(Employment).where(
            Employment.business_id == business_id,
            Employment.terminated_at.is_(None),
        )
    )
    active_employees = list(emp_result.scalars().all())

    for emp in active_employees:
        emp.terminated_at = now

    # Deactivate all job postings
    posting_result = await db.execute(
        select(JobPosting).where(
            JobPosting.business_id == business_id,
            JobPosting.is_active.is_(True),
        )
    )
    postings = list(posting_result.scalars().all())
    for posting in postings:
        posting.is_active = False

    # Close the business
    business.closed_at = now

    await db.flush()

    logger.info(
        "Business %r closed by %s. Terminated %d employees.",
        business.name,
        agent.name,
        len(active_employees),
    )

    return {
        "business_id": str(business.id),
        "name": business.name,
        "closed_at": now.isoformat(),
        "employees_terminated": len(active_employees),
    }
