"""
Business registration and management service for Agent Economy.

Handles the lifecycle of businesses:
  - register_business: create a new business (requires housing, costs money)
  - close_business: close a business and terminate all employees
  - configure_production: set the product a business is assigned to produce
  - set_prices: upsert storefront prices for NPC sales
  - get_business: retrieve a business with its details

Business invariants:
  - Owner must have housing to register a business
  - Zone must allow the business type (if zone has restrictions)
  - Registration costs are deducted immediately
  - Businesses own their own inventory (separate from owner's personal inventory)
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.business import Business, Employment, JobPosting, StorefrontPrice
from backend.models.recipe import Recipe
from backend.models.transaction import Transaction
from backend.models.zone import Zone

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def register_business(
    db: AsyncSession,
    agent: Agent,
    name: str,
    type_slug: str,
    zone_slug: str,
    settings: "Settings",
    clock: "Clock",
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
        raise ValueError(
            "You must have housing before registering a business. "
            "Call rent_housing(zone) first."
        )

    # Look up zone
    result = await db.execute(select(Zone).where(Zone.slug == zone_slug))
    zone = result.scalar_one_or_none()
    if zone is None:
        raise ValueError(f"Zone not found: {zone_slug!r}")

    # Check zone allows this business type
    if zone.allowed_business_types is not None:
        if type_slug not in zone.allowed_business_types:
            raise ValueError(
                f"Zone {zone.name!r} does not allow business type {type_slug!r}. "
                f"Allowed types: {zone.allowed_business_types}"
            )

    # Check registration cost
    reg_cost = Decimal(str(settings.economy.business_registration_cost))
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
        agent.name, name, type_slug, zone_slug, float(reg_cost),
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
    clock: "Clock",
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
        business.name, agent.name, len(active_employees),
    )

    return {
        "business_id": str(business.id),
        "name": business.name,
        "closed_at": now.isoformat(),
        "employees_terminated": len(active_employees),
    }


async def configure_production(
    db: AsyncSession,
    agent: Agent,
    business_id: uuid.UUID,
    product_slug: str,
) -> dict:
    """
    Configure what product a business is set up to produce.

    This is mainly informational — it validates that a recipe exists for
    the product. The actual production happens when workers call work().
    Workers' job postings define the product they produce.

    Args:
        db:           Active async database session.
        agent:        The requesting agent (must be business owner).
        business_id:  UUID of the business.
        product_slug: Good slug of the product to configure.

    Returns:
        Dict with business and recipe information.

    Raises:
        ValueError: If business not found, not owner, or no recipe for product.
    """
    # Verify business ownership
    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()

    if business is None:
        raise ValueError(f"Business not found: {business_id}")

    if business.owner_id != agent.id:
        raise ValueError("You can only configure your own businesses.")

    if not business.is_open():
        raise ValueError(f"Business {business.name!r} is closed.")

    # Verify a recipe exists that produces this good
    recipe_result = await db.execute(
        select(Recipe).where(Recipe.output_good == product_slug)
    )
    recipes = list(recipe_result.scalars().all())

    if not recipes:
        raise ValueError(
            f"No recipe found that produces {product_slug!r}. "
            f"Check recipes.yaml for available production recipes."
        )

    # Return the available recipes for this product
    recipe_list = [r.to_dict() for r in recipes]

    # Check if business type bonus applies to any recipe
    bonus_recipes = [r for r in recipes if r.bonus_business_type == business.type_slug]

    return {
        "business_id": str(business.id),
        "business_name": business.name,
        "business_type": business.type_slug,
        "product_slug": product_slug,
        "available_recipes": recipe_list,
        "bonus_applies": len(bonus_recipes) > 0,
        "bonus_recipes": [r["slug"] for r in recipe_list if r.get("bonus_business_type") == business.type_slug],
        "_hints": {
            "message": (
                f"Business {business.name!r} is configured to produce {product_slug!r}. "
                + (
                    f"Bonus: {business.type_slug} gets faster production on {len(bonus_recipes)} recipe(s)."
                    if bonus_recipes
                    else f"No bonus for {business.type_slug} on this product — consider matching business type to recipe."
                )
            )
        },
    }


async def set_prices(
    db: AsyncSession,
    agent: Agent,
    business_id: uuid.UUID,
    good_slug: str,
    price: float,
) -> dict:
    """
    Set or update the storefront price for a good at a business.

    Upserts the StorefrontPrice record. NPC consumers will buy at this
    price during the fast tick. Price must be positive.

    Args:
        db:          Active async database session.
        agent:       The requesting agent (must be business owner).
        business_id: UUID of the business.
        good_slug:   The good to price.
        price:       Price per unit (must be > 0).

    Returns:
        Dict with updated price info.

    Raises:
        ValueError: If business not found, not owner, or invalid price.
    """
    if price <= 0:
        raise ValueError(f"Price must be positive, got {price}")

    # Verify business ownership
    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()

    if business is None:
        raise ValueError(f"Business not found: {business_id}")

    if business.owner_id != agent.id:
        raise ValueError("You can only set prices for your own businesses.")

    if not business.is_open():
        raise ValueError(f"Business {business.name!r} is closed.")

    # Upsert the storefront price
    existing_result = await db.execute(
        select(StorefrontPrice).where(
            StorefrontPrice.business_id == business_id,
            StorefrontPrice.good_slug == good_slug,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing is None:
        sp = StorefrontPrice(
            business_id=business_id,
            good_slug=good_slug,
            price=Decimal(str(price)),
        )
        db.add(sp)
        action = "created"
    else:
        existing.price = Decimal(str(price))
        sp = existing
        action = "updated"

    await db.flush()

    logger.info(
        "Business %r: %s price for %s = %.2f",
        business.name, action, good_slug, price,
    )

    return {
        "business_id": str(business.id),
        "business_name": business.name,
        "good_slug": good_slug,
        "price": float(price),
        "action": action,
    }


async def get_business(
    db: AsyncSession,
    business_id: uuid.UUID,
) -> dict:
    """
    Retrieve a business with its details.

    Returns basic business information including storefront prices
    and active job postings.

    Args:
        db:          Active async database session.
        business_id: UUID of the business.

    Returns:
        Dict with business details.

    Raises:
        ValueError: If business not found.
    """
    result = await db.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()

    if business is None:
        raise ValueError(f"Business not found: {business_id}")

    # Get storefront prices
    prices_result = await db.execute(
        select(StorefrontPrice).where(StorefrontPrice.business_id == business_id)
    )
    prices = list(prices_result.scalars().all())

    # Get active job postings
    postings_result = await db.execute(
        select(JobPosting).where(
            JobPosting.business_id == business_id,
            JobPosting.is_active.is_(True),
        )
    )
    postings = list(postings_result.scalars().all())

    # Get active employees
    emp_result = await db.execute(
        select(Employment).where(
            Employment.business_id == business_id,
            Employment.terminated_at.is_(None),
        )
    )
    employees = list(emp_result.scalars().all())

    return {
        **business.to_dict(),
        "storefront_prices": [p.to_dict() for p in prices],
        "job_postings": [p.to_dict() for p in postings],
        "active_employee_count": len(employees),
    }
