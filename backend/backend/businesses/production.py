"""
Production (work) logic for Agent Economy.

The work() function is the core of the production system. It routes by
context — if the agent is employed, they produce for their employer; if
they own a business, they produce for themselves.

Flow:
  1. Determine context (employed vs self-employed)
  2. Get the recipe for the assigned product
  3. Check per-agent global cooldown in Redis
  4. Verify the business has enough input materials
  5. Deduct inputs from business inventory
  6. Add outputs to business inventory (respecting storage limits)
  7. If employed: deduct wage from owner's balance, credit to worker
  8. Calculate effective cooldown (base × bonuses × penalties)
  9. Set cooldown in Redis
  10. Return result

Cooldown calculation (all multipliers stack):
  base = recipe.cooldown_seconds
  × bonus_cooldown_multiplier (if business type matches recipe bonus)
  × commute_penalty_multiplier (if agent lives in different zone than business)
  × government production_cooldown_modifier (future Phase 6)

All cooldowns use clock timestamps (same pattern as gathering.py).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.inventory import add_to_inventory, remove_from_inventory
from backend.models.agent import Agent
from backend.models.business import Business, Employment, JobPosting
from backend.models.inventory import InventoryItem
from backend.models.recipe import Recipe
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


def _work_cooldown_key(agent_id: uuid.UUID) -> str:
    """Redis key for the per-agent global work cooldown expiry timestamp."""
    return f"cooldown:work:{agent_id}"


async def work(
    db: AsyncSession,
    redis: "aioredis.Redis",
    agent: Agent,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Perform one unit of production work.

    Routes automatically by context:
    - If the agent has an active Employment → work for employer
    - If the agent owns an open Business → work as self-employed
    - Otherwise → error

    This is the primary income mechanism for workers and business owners.
    Each call produces goods immediately and sets a per-agent cooldown.

    Args:
        db:       Active async database session.
        redis:    Redis client for cooldown tracking.
        agent:    The working agent.
        clock:    Clock for cooldown timestamps.
        settings: Application settings.

    Returns:
        Dict with produced goods, cooldown, and wage info.

    Raises:
        ValueError: If agent is not employed and has no business, on cooldown,
                    or if the business lacks input materials.
    """
    now = clock.now()

    # -----------------------------------------------------------------------
    # Step 1: Determine context — employed or self-employed
    # -----------------------------------------------------------------------
    emp_result = await db.execute(
        select(Employment).where(
            Employment.agent_id == agent.id,
            Employment.terminated_at.is_(None),
        )
    )
    employment = emp_result.scalar_one_or_none()

    business: Business | None = None
    is_employed: bool = False

    if employment is not None:
        # Agent is employed — produce for their employer
        is_employed = True
        biz_result = await db.execute(
            select(Business).where(Business.id == employment.business_id)
        )
        business = biz_result.scalar_one_or_none()

        if business is None or not business.is_open():
            raise ValueError(
                "Your employer's business is no longer open. "
                "Use manage_employees(action='quit_job') to leave, then find new work."
            )

        product_slug = employment.product_slug

    else:
        # Check if agent owns any open businesses
        owned_result = await db.execute(
            select(Business).where(
                Business.owner_id == agent.id,
                Business.closed_at.is_(None),
            ).limit(1)
        )
        business = owned_result.scalar_one_or_none()

        if business is None:
            raise ValueError(
                "You are not employed and have no open business. "
                "Apply for a job with apply_job(job_id) or register a business "
                "with register_business(name, type, zone)."
            )

        # Self-employed: use configure_production() setting first, then
        # fall back to active job postings.  (configure_production is the
        # owner's explicit choice; job postings may have been set up for
        # employees and should not override the owner's preference.)
        if business.default_recipe_slug is not None:
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

    # -----------------------------------------------------------------------
    # Step 2: Get the recipe for the product
    # -----------------------------------------------------------------------
    recipe_result = await db.execute(
        select(Recipe).where(Recipe.output_good == product_slug)
    )
    # Prefer recipe that matches business type bonus
    recipes = list(recipe_result.scalars().all())

    if not recipes:
        raise ValueError(
            f"No recipe found for product {product_slug!r}. "
            "Check recipes.yaml for available production recipes."
        )

    # Pick the best recipe: bonus recipe for this business type first
    recipe: Recipe | None = None
    for r in recipes:
        if r.bonus_business_type == business.type_slug:
            recipe = r
            break
    if recipe is None:
        recipe = recipes[0]

    # -----------------------------------------------------------------------
    # Step 3: Check per-agent global work cooldown in Redis
    # -----------------------------------------------------------------------
    # Acquire a processing lock atomically to prevent concurrent work races
    lock_key = f"lock:work:{agent.id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=300)  # 5 min safety TTL
    if not acquired:
        raise ValueError("Work already in progress. Try again shortly.")

    cooldown_key = _work_cooldown_key(agent.id)
    stored_expiry = await redis.get(cooldown_key)

    if stored_expiry:
        try:
            expiry_dt = datetime.fromisoformat(stored_expiry)
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
            if now < expiry_dt:
                remaining = int((expiry_dt - now).total_seconds())
                await redis.delete(lock_key)
                raise ValueError(
                    f"Work cooldown active. Try again in {remaining} seconds. "
                    f"(Producing: {product_slug})"
                )
        except (ValueError, TypeError) as e:
            if "cooldown active" in str(e).lower():
                raise
            # Corrupted key — ignore
            logger.warning("Corrupted work cooldown key %s: %r", cooldown_key, stored_expiry)

    # -----------------------------------------------------------------------
    # Step 4: Verify the business has enough input materials
    # -----------------------------------------------------------------------
    try:
        inputs = recipe.inputs_json or []

        for inp in inputs:
            good_slug = inp["good_slug"]
            required_qty = inp["quantity"]

            inv_result = await db.execute(
                select(InventoryItem).where(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == business.id,
                    InventoryItem.good_slug == good_slug,
                )
            )
            inv_item = inv_result.scalar_one_or_none()
            have = inv_item.quantity if inv_item else 0

            if have < required_qty:
                raise ValueError(
                    f"Business {business.name!r} lacks inputs to produce {product_slug!r}. "
                    f"Need {required_qty}x {good_slug}, have {have}. "
                    f"Use POST /v1/businesses/inventory with action='deposit' to transfer "
                    f"goods from your personal inventory to the business."
                )

        # -------------------------------------------------------------------
        # Step 5: Deduct inputs from business inventory
        # -------------------------------------------------------------------
        for inp in inputs:
            await remove_from_inventory(
                db=db,
                owner_type="business",
                owner_id=business.id,
                good_slug=inp["good_slug"],
                quantity=inp["quantity"],
            )

        # -------------------------------------------------------------------
        # Step 6: Add outputs to business inventory
        # -------------------------------------------------------------------
        try:
            output_item = await add_to_inventory(
                db=db,
                owner_type="business",
                owner_id=business.id,
                good_slug=recipe.output_good,
                quantity=recipe.output_quantity,
                settings=settings,
            )
        except ValueError as e:
            # Storage full — roll back the input deductions by re-adding them
            # This shouldn't happen often; warn and propagate
            logger.warning(
                "Business %r storage full during work() — re-adding inputs (this is unusual)",
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
            ) from e

        # -------------------------------------------------------------------
        # Step 7: Pay wage (if employed)
        # -------------------------------------------------------------------
        wage_earned: float = 0.0
        owner_agent: Agent | None = None

        if is_employed:
            wage = Decimal(str(employment.wage_per_work))
            wage_earned = float(wage)

            # Look up the business owner to deduct from their balance
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
                    f"Business owner not found. Cannot process wage payment. "
                    f"Contact the business owner."
                )

            # Re-lock worker agent before wage credit (prevent concurrent balance mutation)
            worker_row = await db.execute(
                select(Agent).where(Agent.id == agent.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            agent = worker_row.scalar_one()

            owner_balance = Decimal(str(owner_agent.balance))

            # Check owner can afford the wage
            if owner_balance < wage:
                raise ValueError(
                    f"Business owner has insufficient funds to pay wage. "
                    f"Needed {float(wage):.2f}, owner has {float(owner_balance):.2f}. "
                    f"Ask the business owner to deposit more funds."
                )

            # Deduct from owner, credit to worker
            owner_agent.balance = owner_balance - wage
            agent.balance = Decimal(str(agent.balance)) + wage

            # Record wage transaction
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

        # -------------------------------------------------------------------
        # Step 8: Calculate effective cooldown
        # -------------------------------------------------------------------
        base_cooldown = recipe.cooldown_seconds

        # Business type bonus
        if (
            recipe.bonus_business_type is not None
            and recipe.bonus_business_type == business.type_slug
            and recipe.bonus_cooldown_multiplier < 1.0
        ):
            bonus_multiplier = recipe.bonus_cooldown_multiplier
            bonus_applied = True
        else:
            bonus_multiplier = 1.0
            bonus_applied = False

        # Commute penalty: if agent housing zone != business zone
        commute_penalty_applied = False
        if agent.housing_zone_id is not None and agent.housing_zone_id != business.zone_id:
            commute_multiplier = settings.economy.commute_cooldown_multiplier
            commute_penalty_applied = True
        else:
            commute_multiplier = 1.0

        # Government production cooldown modifier
        government_modifier = await _get_government_modifier(db, settings)

        # Homeless penalty: homeless agents produce at reduced efficiency
        # (cooldown multiplied by 1/penalty, e.g. penalty=0.5 → cooldown doubles)
        homeless_penalty_multiplier = 1.0
        if agent.is_homeless():
            homeless_efficiency_penalty = getattr(
                settings.economy, "housing_homeless_efficiency_penalty", 0.5
            )
            if homeless_efficiency_penalty > 0:
                homeless_penalty_multiplier = 1.0 / homeless_efficiency_penalty

        effective_cooldown = int(
            base_cooldown
            * bonus_multiplier
            * commute_multiplier
            * government_modifier
            * homeless_penalty_multiplier
        )
        # Minimum 1 second cooldown
        effective_cooldown = max(1, effective_cooldown)

        # -------------------------------------------------------------------
        # Step 9: Set cooldown in Redis (clock-based timestamp)
        # -------------------------------------------------------------------
        expiry_time = now + timedelta(seconds=effective_cooldown)
        expiry_str = expiry_time.isoformat()

        # Real-time TTL = 2x cooldown as safety buffer
        real_ttl = max(effective_cooldown * 2, 120)
        await redis.set(cooldown_key, expiry_str, ex=real_ttl)

        await db.flush()
    finally:
        await redis.delete(lock_key)

    logger.info(
        "Agent %s worked at %r: produced %dx %s (cooldown=%ds, employed=%s, wage=%.2f)",
        agent.name, business.name, recipe.output_quantity, recipe.output_good,
        effective_cooldown, is_employed, wage_earned,
    )

    # -----------------------------------------------------------------------
    # Step 10: Return result
    # -----------------------------------------------------------------------
    result = {
        "produced": {
            "good": recipe.output_good,
            "quantity": recipe.output_quantity,
            "new_business_inventory": output_item.quantity,
        },
        "inputs_consumed": inputs,
        "recipe_slug": recipe.slug,
        "business_id": str(business.id),
        "business_name": business.name,
        "cooldown_seconds": effective_cooldown,
        "cooldown_breakdown": {
            "base": base_cooldown,
            "bonus_applied": bonus_applied,
            "bonus_multiplier": bonus_multiplier if bonus_applied else None,
            "commute_penalty_applied": commute_penalty_applied,
            "commute_multiplier": commute_multiplier if commute_penalty_applied else None,
            "government_modifier": government_modifier if government_modifier != 1.0 else None,
            "homeless_penalty_applied": homeless_penalty_multiplier != 1.0,
            "homeless_penalty_multiplier": homeless_penalty_multiplier if homeless_penalty_multiplier != 1.0 else None,
        },
        "employed": is_employed,
        "_hints": {
            "check_back_seconds": effective_cooldown,
            "message": (
                f"Produced {recipe.output_quantity}x {recipe.output_good}. "
                f"Next work call available in {effective_cooldown}s."
            ),
        },
    }

    if is_employed:
        result["wage_earned"] = wage_earned
        result["new_balance"] = float(agent.balance)

    return result


async def _get_government_modifier(db: AsyncSession, settings: "Settings") -> float:
    """
    Get the current government production_cooldown_modifier.

    Queries GovernmentState for the current template's production_cooldown_modifier.
    Falls back to 1.0 if government tables don't exist or state is missing.
    """
    try:
        from backend.models.government import GovernmentState
        from backend.government.service import get_policy_params
        result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
        govt = result.scalar_one_or_none()
        if not govt:
            return 1.0
        params = get_policy_params(settings, govt.current_template_slug)
        return float(params.get("production_cooldown_modifier", 1.0))
    except Exception:
        return 1.0


async def get_work_cooldown_remaining(
    redis: "aioredis.Redis",
    agent: Agent,
    clock: "Clock",
) -> int | None:
    """
    Check how many seconds remain on an agent's work cooldown.

    Returns None if not on cooldown, or the remaining seconds if active.

    Used by get_status to show cooldown info.
    """
    cooldown_key = _work_cooldown_key(agent.id)
    stored_expiry = await redis.get(cooldown_key)

    if not stored_expiry:
        return None

    try:
        expiry_dt = datetime.fromisoformat(stored_expiry)
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        now = clock.now()
        if now < expiry_dt:
            return int((expiry_dt - now).total_seconds())
    except (ValueError, TypeError):
        pass

    return None
