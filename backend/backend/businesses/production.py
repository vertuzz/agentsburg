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
  8. Calculate effective cooldown (base x bonuses x penalties)
  9. Set cooldown in Redis
  10. Return result

Cooldown calculation (all multipliers stack):
  base = recipe.cooldown_seconds
  x bonus_cooldown_multiplier (if business type matches recipe bonus)
  x commute_penalty_multiplier (if agent lives in different zone than business)
  x government production_cooldown_modifier

All cooldowns use clock timestamps (same pattern as gathering.py).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.businesses.recipes import (  # noqa: F401
    _work_cooldown_key,
    get_work_cooldown_remaining,
)
from backend.businesses.work_context import (
    pay_wage,
    produce_output,
    resolve_work_context,
    select_recipe,
    verify_and_consume_inputs,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent
    from backend.models.business import Business
    from backend.models.recipe import Recipe

logger = logging.getLogger(__name__)


async def work(
    db: AsyncSession,
    redis: aioredis.Redis,
    agent: Agent,
    clock: Clock,
    settings: Settings,
    business_id: str | None = None,
) -> dict:
    """
    Perform one unit of production work.

    Routes automatically by context:
    - If the agent has an active Employment -> work for employer
    - If the agent owns an open Business -> work as self-employed
    - Otherwise -> error

    Returns dict with produced goods, cooldown, and wage info.
    """
    now = clock.now()

    # Step 1: Determine context — employed or self-employed
    ctx = await resolve_work_context(db, agent, business_id=business_id)

    # Step 2: Get the recipe for the product
    recipe = await select_recipe(db, ctx.product_slug, ctx.business.type_slug)

    # Step 3: Check per-agent global work cooldown in Redis
    lock_key = f"lock:work:{agent.id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=300)
    if not acquired:
        raise ValueError("Work already in progress. Try again shortly.")

    cooldown_key = _work_cooldown_key(agent.id)
    stored_expiry = await redis.get(cooldown_key)

    if stored_expiry:
        try:
            expiry_dt = datetime.fromisoformat(stored_expiry)
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=UTC)
            if now < expiry_dt:
                remaining = int((expiry_dt - now).total_seconds())
                await redis.delete(lock_key)
                raise ValueError(
                    f"Work cooldown active. Try again in {remaining} seconds. (Producing: {ctx.product_slug})"
                )
        except (ValueError, TypeError) as e:
            if "cooldown active" in str(e).lower():
                raise
            logger.warning("Corrupted work cooldown key %s: %r", cooldown_key, stored_expiry)

    # Steps 4-9: Production, payment, and cooldown (under lock)
    try:
        inputs = await verify_and_consume_inputs(db, ctx.business, recipe)
        output_item = await produce_output(db, ctx.business, recipe, inputs, settings)

        wage_earned: float = 0.0
        if ctx.is_employed:
            wage_earned, agent = await pay_wage(
                db,
                agent,
                ctx.employment,
                ctx.business,
                ctx.product_slug,
                recipe,
                now,
            )

        # Step 8: Calculate effective cooldown
        cd = await _calc_cooldown(db, agent, ctx.business, recipe, settings)

        # Step 9: Set cooldown in Redis
        expiry_time = now + timedelta(seconds=cd["effective"])
        real_ttl = max(cd["effective"] * 2, 120)
        await redis.set(cooldown_key, expiry_time.isoformat(), ex=real_ttl)

        await db.flush()
    finally:
        await redis.delete(lock_key)

    logger.info(
        "Agent %s worked at %r: produced %dx %s (cooldown=%ds, employed=%s, wage=%.2f)",
        agent.name,
        ctx.business.name,
        recipe.output_quantity,
        recipe.output_good,
        cd["effective"],
        ctx.is_employed,
        wage_earned,
    )

    # Step 10: Return result
    result = {
        "produced": {
            "good": recipe.output_good,
            "quantity": recipe.output_quantity,
            "new_business_inventory": output_item.quantity,
        },
        "inputs_consumed": inputs,
        "recipe_slug": recipe.slug,
        "business_id": str(ctx.business.id),
        "business_name": ctx.business.name,
        "cooldown_seconds": cd["effective"],
        "cooldown_breakdown": {
            "base": cd["base"],
            "bonus_applied": cd["bonus_applied"],
            "bonus_multiplier": cd["bonus_mult"] if cd["bonus_applied"] else None,
            "commute_penalty_applied": cd["commute_applied"],
            "commute_multiplier": cd["commute_mult"] if cd["commute_applied"] else None,
            "government_modifier": cd["govt_mod"] if cd["govt_mod"] != 1.0 else None,
            "homeless_penalty_applied": cd["homeless_applied"],
            "homeless_penalty_multiplier": cd["homeless_mult"] if cd["homeless_applied"] else None,
        },
        "employed": ctx.is_employed,
        "_hints": {
            "check_back_seconds": cd["effective"],
            "message": (
                f"Produced {recipe.output_quantity}x {recipe.output_good}. "
                f"Next work call available in {cd['effective']}s."
            ),
        },
    }

    if ctx.is_employed:
        result["wage_earned"] = wage_earned
        result["new_balance"] = float(agent.balance)

    return result


async def _calc_cooldown(
    db: AsyncSession,
    agent: Agent,
    business: Business,
    recipe: Recipe,
    settings: Settings,
) -> dict:
    """Calculate effective work cooldown with all multipliers."""
    base = recipe.cooldown_seconds

    if (
        recipe.bonus_business_type is not None
        and recipe.bonus_business_type == business.type_slug
        and recipe.bonus_cooldown_multiplier < 1.0
    ):
        bonus_mult = recipe.bonus_cooldown_multiplier
        bonus_applied = True
    else:
        bonus_mult = 1.0
        bonus_applied = False

    commute_applied = False
    if agent.housing_zone_id is not None and agent.housing_zone_id != business.zone_id:
        commute_mult = settings.economy.commute_cooldown_multiplier
        commute_applied = True
    else:
        commute_mult = 1.0

    govt_mod = await _get_government_modifier(db, settings)

    homeless_mult = 1.0
    if agent.is_homeless():
        penalty = getattr(settings.economy, "housing_homeless_efficiency_penalty", 0.5)
        if penalty > 0:
            homeless_mult = 1.0 / penalty

    effective = max(1, int(base * bonus_mult * commute_mult * govt_mod * homeless_mult))

    return {
        "effective": effective,
        "base": base,
        "bonus_applied": bonus_applied,
        "bonus_mult": bonus_mult,
        "commute_applied": commute_applied,
        "commute_mult": commute_mult,
        "govt_mod": govt_mod,
        "homeless_applied": homeless_mult != 1.0,
        "homeless_mult": homeless_mult,
    }


async def _get_government_modifier(db: AsyncSession, settings: Settings) -> float:
    """Get the current government production_cooldown_modifier (default 1.0)."""
    try:
        from backend.government.service import get_policy_params
        from backend.models.government import GovernmentState

        result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
        govt = result.scalar_one_or_none()
        if not govt:
            return 1.0
        params = get_policy_params(settings, govt.current_template_slug)
        return float(params.get("production_cooldown_modifier", 1.0))
    except Exception:
        return 1.0
