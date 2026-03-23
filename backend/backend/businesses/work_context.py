"""
Work context helpers for production.

Extracted from production.py to keep each module under 300 lines.
Contains helpers for:
  - Resolving work context (employed vs self-employed)
  - Selecting the best recipe for a business
  - Verifying and consuming input materials
  - Producing output goods
  - Paying wages
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.inventory import add_to_inventory, remove_from_inventory
from backend.models.agent import Agent
from backend.models.business import Business, Employment, JobPosting
from backend.models.inventory import InventoryItem
from backend.models.recipe import Recipe
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from datetime import datetime

    from backend.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class WorkContext:
    """Result of resolving the work context for an agent."""

    business: Business
    product_slug: str
    is_employed: bool
    employment: Employment | None = None


async def resolve_work_context(
    db: AsyncSession, agent: Agent, business_id: str | None = None,
) -> WorkContext:
    """Determine whether the agent is employed or self-employed."""
    emp_result = await db.execute(
        select(Employment).where(
            Employment.agent_id == agent.id,
            Employment.terminated_at.is_(None),
        )
    )
    employment = emp_result.scalar_one_or_none()

    if employment is not None:
        biz_result = await db.execute(
            select(Business).where(Business.id == employment.business_id)
        )
        business = biz_result.scalar_one_or_none()
        if business is None or not business.is_open():
            raise ValueError(
                "Your employer's business is no longer open. "
                "Use manage_employees(action='quit_job') to leave, then find new work."
            )
        return WorkContext(
            business=business, product_slug=employment.product_slug,
            is_employed=True, employment=employment,
        )

    if business_id is not None:
        import uuid as _uuid
        try:
            biz_uuid = _uuid.UUID(business_id)
        except ValueError:
            raise ValueError(f"Invalid business_id: {business_id!r}")
        owned_result = await db.execute(
            select(Business).where(
                Business.id == biz_uuid,
                Business.owner_id == agent.id,
                Business.closed_at.is_(None),
            )
        )
        business = owned_result.scalar_one_or_none()
        if business is None:
            raise ValueError(
                f"Business {business_id!r} not found, not owned by you, or closed."
            )
    else:
        owned_result = await db.execute(
            select(Business).where(
                Business.owner_id == agent.id, Business.closed_at.is_(None),
            ).limit(1)
        )
        business = owned_result.scalar_one_or_none()
    if business is None:
        raise ValueError(
            "You are not employed and have no open business. "
            "Apply for a job with apply_job(job_id) or register a business "
            "with register_business(name, type, zone)."
        )

    if business.default_recipe_slug is not None:
        # Try direct recipe slug lookup first (new format).
        # Fall back to output_good lookup for backward compat with old data
        # that stored good slugs instead of recipe slugs.
        recipe_result = await db.execute(
            select(Recipe).where(Recipe.slug == business.default_recipe_slug)
        )
        direct_recipe = recipe_result.scalar_one_or_none()
        if direct_recipe is not None:
            product_slug = direct_recipe.output_good
        else:
            product_slug = business.default_recipe_slug
    else:
        jp_result = await db.execute(
            select(JobPosting).where(
                JobPosting.business_id == business.id,
                JobPosting.is_active.is_(True),
            ).limit(1)
        )
        job_posting = jp_result.scalar_one_or_none()
        if job_posting is not None:
            product_slug = job_posting.product_slug
        else:
            raise ValueError(
                f"Business {business.name!r} has no production configured. "
                "Call configure_production(business_id, product='...') to set what "
                "to produce, then call work()."
            )

    return WorkContext(
        business=business, product_slug=product_slug,
        is_employed=False, employment=None,
    )


async def select_recipe(
    db: AsyncSession, product_slug: str, business_type: str,
) -> Recipe:
    """Pick the best recipe for a product, preferring business-type bonuses."""
    recipe_result = await db.execute(
        select(Recipe).where(Recipe.output_good == product_slug)
    )
    recipes = list(recipe_result.scalars().all())
    if not recipes:
        raise ValueError(
            f"No recipe found for product {product_slug!r}. "
            "Check recipes.yaml for available production recipes."
        )
    for r in recipes:
        if r.bonus_business_type == business_type:
            return r
    return recipes[0]


async def verify_and_consume_inputs(
    db: AsyncSession, business: Business, recipe: Recipe,
) -> list[dict[str, Any]]:
    """Verify the business has enough inputs and deduct them."""
    inputs: list[dict[str, Any]] = recipe.inputs_json or []
    for inp in inputs:
        inv_result = await db.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "business",
                InventoryItem.owner_id == business.id,
                InventoryItem.good_slug == inp["good_slug"],
            )
        )
        inv_item = inv_result.scalar_one_or_none()
        have = inv_item.quantity if inv_item else 0
        if have < inp["quantity"]:
            raise ValueError(
                f"Business {business.name!r} lacks inputs to produce {recipe.output_good!r}. "
                f"Need {inp['quantity']}x {inp['good_slug']}, have {have}. "
                f"Use POST /v1/businesses/inventory with action='deposit' to transfer "
                f"goods from your personal inventory to the business."
            )
    for inp in inputs:
        await remove_from_inventory(
            db=db, owner_type="business", owner_id=business.id,
            good_slug=inp["good_slug"], quantity=inp["quantity"],
        )
    return inputs


async def produce_output(
    db: AsyncSession, business: Business, recipe: Recipe,
    inputs: list[dict[str, Any]], settings: "Settings",
) -> InventoryItem:
    """Add produced goods to business inventory; rolls back inputs on storage-full."""
    try:
        return await add_to_inventory(
            db=db, owner_type="business", owner_id=business.id,
            good_slug=recipe.output_good, quantity=recipe.output_quantity,
            settings=settings,
        )
    except ValueError:
        logger.warning(
            "Business %r storage full during work() — re-adding inputs", business.name,
        )
        for inp in inputs:
            await add_to_inventory(
                db=db, owner_type="business", owner_id=business.id,
                good_slug=inp["good_slug"], quantity=inp["quantity"],
                settings=settings,
            )
        raise ValueError(
            f"Business {business.name!r} storage is full. Cannot store {recipe.output_good}. "
            f"Use POST /v1/businesses/inventory with action='withdraw' to move goods "
            f"to personal inventory, or set storefront prices via POST /v1/businesses/prices "
            f"to let NPC consumers buy."
        ) from None


async def pay_wage(
    db: AsyncSession, agent: Agent, employment: Employment,
    business: Business, product_slug: str, recipe: Recipe, now: "datetime",
) -> tuple[float, Agent]:
    """Deduct wage from owner and credit to worker. Returns (wage_earned, agent)."""
    wage = Decimal(str(employment.wage_per_work))

    owner_result = await db.execute(
        select(Agent).where(Agent.id == business.owner_id).with_for_update()
    )
    owner_agent = owner_result.scalar_one_or_none()
    if owner_agent is None:
        logger.error(
            "Business %r has no owner agent (owner_id=%s) — cannot pay wage",
            business.name, business.owner_id,
        )
        raise ValueError(
            "Business owner not found. Cannot process wage payment. "
            "Contact the business owner."
        )

    worker_row = await db.execute(
        select(Agent).where(Agent.id == agent.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    agent = worker_row.scalar_one()

    owner_balance = Decimal(str(owner_agent.balance))
    if owner_balance < wage:
        raise ValueError(
            f"Business owner has insufficient funds to pay wage. "
            f"Needed {float(wage):.2f}, owner has {float(owner_balance):.2f}. "
            f"Ask the business owner to deposit more funds."
        )

    owner_agent.balance = owner_balance - wage
    agent.balance = Decimal(str(agent.balance)) + wage

    txn = Transaction(
        type="wage", from_agent_id=owner_agent.id, to_agent_id=agent.id,
        amount=wage, metadata_json={
            "business_id": str(business.id), "business_name": business.name,
            "product_slug": product_slug, "recipe_slug": recipe.slug,
            "employment_id": str(employment.id), "timestamp": now.isoformat(),
        },
    )
    db.add(txn)
    return float(wage), agent
