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
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from backend.agents.inventory import add_to_inventory, remove_from_inventory
from backend.businesses.recipes import _work_cooldown_key
from backend.models.agent import Agent
from backend.models.business import Business, Employment, JobPosting
from backend.models.inventory import InventoryItem
from backend.models.recipe import Recipe
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
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
    db: AsyncSession,
    agent: Agent,
    business_id: str | None = None,
    redis: aioredis.Redis | None = None,
    clock: Clock | None = None,
) -> WorkContext:
    """Determine whether the agent is employed or self-employed.

    When ``business_id`` is omitted and the agent owns multiple businesses,
    iterates all open businesses and picks the first one that has production
    configured.  When the work cooldown is active, raises a helpful error
    listing all owned businesses so the agent can target a specific one.
    """
    emp_result = await db.execute(
        select(Employment).where(
            Employment.agent_id == agent.id,
            Employment.terminated_at.is_(None),
        )
    )
    employment = emp_result.scalar_one_or_none()

    if employment is not None:
        biz_result = await db.execute(select(Business).where(Business.id == employment.business_id))
        business = biz_result.scalar_one_or_none()
        if business is None or not business.is_open():
            raise ValueError(
                "Your employer's business is no longer open. "
                "Use manage_employees(action='quit_job') to leave, then find new work."
            )
        return WorkContext(
            business=business,
            product_slug=employment.product_slug,
            is_employed=True,
            employment=employment,
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
            raise ValueError(f"Business {business_id!r} not found, not owned by you, or closed.")
    else:
        # Fetch ALL open businesses for this agent
        owned_result = await db.execute(
            select(Business).where(
                Business.owner_id == agent.id,
                Business.closed_at.is_(None),
            )
        )
        owned_businesses = list(owned_result.scalars().all())

        if not owned_businesses:
            raise ValueError(
                "You are not employed and have no open business. "
                "Apply for a job with apply_job(job_id) or register a business "
                "with register_business(name, type, zone)."
            )

        # Smart routing: pick best business (cooldown-aware if redis+clock available)
        if redis is not None and clock is not None and len(owned_businesses) > 1:
            business = await _pick_available_business(owned_businesses, agent, redis, clock)
        else:
            business = owned_businesses[0]

    if business.default_recipe_slug is not None:
        # Try direct recipe slug lookup first (new format).
        # Fall back to output_good lookup for backward compat with old data
        # that stored good slugs instead of recipe slugs.
        recipe_result = await db.execute(select(Recipe).where(Recipe.slug == business.default_recipe_slug))
        direct_recipe = recipe_result.scalar_one_or_none()
        product_slug = direct_recipe.output_good if direct_recipe is not None else business.default_recipe_slug
    else:
        jp_result = await db.execute(
            select(JobPosting)
            .where(
                JobPosting.business_id == business.id,
                JobPosting.is_active.is_(True),
            )
            .limit(1)
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
        business=business,
        product_slug=product_slug,
        is_employed=False,
        employment=None,
    )


async def _pick_available_business(
    businesses: list[Business],
    agent: Agent,
    redis: aioredis.Redis,
    clock: Clock,
) -> Business:
    """Pick the best business when an agent owns multiple.

    The work cooldown is per-agent (global), not per-business.  When the
    cooldown is active, raise a helpful error listing all owned businesses
    so the agent knows to pass ``business_id`` next time.  When the cooldown
    is NOT active, return the first business that has production configured
    (``default_recipe_slug`` set), falling back to the first business.
    """
    now = clock.now()
    cooldown_key = _work_cooldown_key(agent.id)
    stored_expiry = await redis.get(cooldown_key)

    if stored_expiry:
        try:
            expiry_dt = datetime.fromisoformat(stored_expiry)
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=UTC)
        except ValueError, TypeError:
            # Corrupted expiry value — ignore and proceed
            expiry_dt = None

        if expiry_dt is not None and now < expiry_dt:
            remaining = int((expiry_dt - now).total_seconds())
            biz_list = ", ".join(f"{b.name} (id={b.id})" for b in businesses)
            raise ValueError(
                f"Work cooldown active. Try again in {remaining} seconds. "
                f"You own {len(businesses)} businesses: {biz_list}. "
                f"Tip: pass business_id to target a specific business when cooldown expires."
            )

    # No cooldown — prefer a business with production already configured
    for b in businesses:
        if b.default_recipe_slug is not None:
            return b
    return businesses[0]


async def select_recipe(
    db: AsyncSession,
    product_slug: str,
    business_type: str,
) -> Recipe:
    """Pick the best recipe for a product, preferring business-type bonuses."""
    recipe_result = await db.execute(select(Recipe).where(Recipe.output_good == product_slug))
    recipes = list(recipe_result.scalars().all())
    if not recipes:
        raise ValueError(
            f"No recipe found for product {product_slug!r}. Check recipes.yaml for available production recipes."
        )
    for r in recipes:
        if r.bonus_business_type == business_type:
            return r
    return recipes[0]


async def verify_and_consume_inputs(
    db: AsyncSession,
    business: Business,
    recipe: Recipe,
    agent: Agent | None = None,
    settings: Any = None,
) -> list[dict[str, Any]]:
    """Verify the business has enough inputs and deduct them.

    If the business lacks inputs:
      1. If an employed agent has the needed goods, auto-deposit them.
      2. If the business is NPC-owned, auto-restock from the central bank.
    """
    inputs: list[dict[str, Any]] = recipe.inputs_json or []
    auto_deposited: list[dict] = []

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
        shortfall = inp["quantity"] - have

        if shortfall > 0 and agent is not None and settings is not None:
            # Try employee auto-deposit from personal inventory
            agent_inv_result = await db.execute(
                select(InventoryItem).where(
                    InventoryItem.owner_type == "agent",
                    InventoryItem.owner_id == agent.id,
                    InventoryItem.good_slug == inp["good_slug"],
                )
            )
            agent_inv = agent_inv_result.scalar_one_or_none()
            agent_has = agent_inv.quantity if agent_inv else 0

            if agent_has >= shortfall:
                import contextlib

                with contextlib.suppress(ValueError):
                    await remove_from_inventory(
                        db=db,
                        owner_type="agent",
                        owner_id=agent.id,
                        good_slug=inp["good_slug"],
                        quantity=shortfall,
                    )
                    await add_to_inventory(
                        db=db,
                        owner_type="business",
                        owner_id=business.id,
                        good_slug=inp["good_slug"],
                        quantity=shortfall,
                        settings=settings,
                    )
                    auto_deposited.append({"good_slug": inp["good_slug"], "quantity": shortfall})
                    shortfall = 0

        if shortfall > 0 and business.is_npc and settings is not None:
            # NPC auto-restock from central bank
            await _npc_restock_input(db, business, inp["good_slug"], shortfall, settings)
            shortfall = 0

        if shortfall > 0:
            raise ValueError(
                f"Business {business.name!r} lacks inputs to produce {recipe.output_good!r}. "
                f"Need {inp['quantity']}x {inp['good_slug']}, have {have}. "
                f"Use POST /v1/businesses/inventory with action='deposit' to transfer "
                f"goods from your personal inventory to the business."
            )

    for inp in inputs:
        await remove_from_inventory(
            db=db,
            owner_type="business",
            owner_id=business.id,
            good_slug=inp["good_slug"],
            quantity=inp["quantity"],
        )
    return inputs


async def _npc_restock_input(
    db: AsyncSession,
    business: Business,
    good_slug: str,
    quantity: int,
    settings: Any,
) -> None:
    """Buy missing inputs from the central bank at base_value for NPC businesses."""
    from backend.models.banking import CentralBank

    goods_config = {g["slug"]: g for g in settings.goods}
    unit_cost = Decimal(str(goods_config.get(good_slug, {}).get("base_value", 1)))
    total_cost = unit_cost * quantity

    bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1).with_for_update())
    central_bank = bank_result.scalar_one_or_none()
    if central_bank is None or Decimal(str(central_bank.reserves)) < total_cost:
        return

    central_bank.reserves = Decimal(str(central_bank.reserves)) - total_cost

    import contextlib

    with contextlib.suppress(ValueError):
        await add_to_inventory(
            db=db,
            owner_type="business",
            owner_id=business.id,
            good_slug=good_slug,
            quantity=quantity,
            settings=settings,
        )
    await db.flush()


async def produce_output(
    db: AsyncSession,
    business: Business,
    recipe: Recipe,
    inputs: list[dict[str, Any]],
    settings: Settings,
) -> InventoryItem:
    """Add produced goods to business inventory; rolls back inputs on storage-full."""
    try:
        return await add_to_inventory(
            db=db,
            owner_type="business",
            owner_id=business.id,
            good_slug=recipe.output_good,
            quantity=recipe.output_quantity,
            settings=settings,
        )
    except ValueError:
        logger.warning(
            "Business %r storage full during work() — re-adding inputs",
            business.name,
        )
        for inp in inputs:
            await add_to_inventory(
                db=db,
                owner_type="business",
                owner_id=business.id,
                good_slug=inp["good_slug"],
                quantity=inp["quantity"],
                settings=settings,
            )
        raise ValueError(
            f"Business {business.name!r} storage is full. Cannot store {recipe.output_good}. "
            f"Use POST /v1/businesses/inventory with action='withdraw' to move goods "
            f"to personal inventory, or set storefront prices via POST /v1/businesses/prices "
            f"to let NPC consumers buy."
        ) from None


async def pay_wage(
    db: AsyncSession,
    agent: Agent,
    employment: Employment,
    business: Business,
    product_slug: str,
    recipe: Recipe,
    now: datetime,
) -> tuple[float, Agent]:
    """Deduct wage from owner and credit to worker. Returns (wage_earned, agent)."""
    wage = Decimal(str(employment.wage_per_work))

    owner_result = await db.execute(select(Agent).where(Agent.id == business.owner_id).with_for_update())
    owner_agent = owner_result.scalar_one_or_none()
    if owner_agent is None:
        logger.error(
            "Business %r has no owner agent (owner_id=%s) — cannot pay wage",
            business.name,
            business.owner_id,
        )
        raise ValueError("Business owner not found. Cannot process wage payment. Contact the business owner.")

    worker_row = await db.execute(
        select(Agent).where(Agent.id == agent.id).with_for_update().execution_options(populate_existing=True)
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
        type="wage",
        from_agent_id=owner_agent.id,
        to_agent_id=agent.id,
        amount=wage,
        metadata_json={
            "business_id": str(business.id),
            "business_name": business.name,
            "product_slug": product_slug,
            "recipe_slug": recipe.slug,
            "employment_id": str(employment.id),
            "timestamp": now.isoformat(),
        },
    )
    db.add(txn)
    return float(wage), agent
