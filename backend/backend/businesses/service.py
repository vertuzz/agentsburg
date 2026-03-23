"""
Business management service for Agent Economy.

Handles configuration and pricing of businesses.
Registration and closing logic is in registration.py.
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

# Re-exports for backwards compatibility
from backend.businesses.registration import (  # noqa: F401
    close_business,
    register_business,
)

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


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

    # Persist the actual recipe slug (not the good slug) so work() can
    # look it up directly and avoid ambiguity when multiple recipes
    # produce the same good.
    bonus_recipes = [r for r in recipes if r.bonus_business_type == business.type_slug]
    best_recipe = bonus_recipes[0] if bonus_recipes else recipes[0]
    business.default_recipe_slug = best_recipe.slug
    await db.flush()

    # Return the available recipes for this product
    recipe_list = [r.to_dict() for r in recipes]

    return {
        "business_id": str(business.id),
        "business_name": business.name,
        "business_type": business.type_slug,
        "product_slug": product_slug,
        "selected_recipe": best_recipe.slug,
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
    """Retrieve a business with its details."""
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
