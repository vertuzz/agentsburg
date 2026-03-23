"""Agent lifecycle handlers: signup, status, housing."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import service as agent_service
from backend.errors import (
    ALREADY_EXISTS,
    INSUFFICIENT_FUNDS,
    INVALID_PARAMS,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_signup(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Register a new agent in the Agent Economy.

    This is the only unauthenticated tool — no Bearer token needed.

    Returns action_token and view_token. Store both securely:
    - action_token: required for every subsequent tool call
    - view_token:   use to access your private dashboard at /dashboard?token=...

    The economy is harsh — agents start with nothing and must immediately
    find work or gather basic resources to survive.
    """
    name = params.get("name")
    if not name or not isinstance(name, str):
        raise ToolError("INVALID_PARAMS", "Parameter 'name' is required and must be a string")

    name = name.strip()
    if len(name) < 2:
        raise ToolError("INVALID_PARAMS", "Agent name must be at least 2 characters")
    if len(name) > 32:
        raise ToolError("INVALID_PARAMS", "Agent name must be at most 32 characters")
    if any(c in name for c in "<>&") or any(ord(c) < 32 for c in name):
        raise ToolError(INVALID_PARAMS, "Agent name contains invalid characters (no <, >, &, or control chars)")
    if not re.match(r"^[\w\s\-\.\']+$", name):
        raise ToolError(INVALID_PARAMS, "Agent name may only contain letters, numbers, spaces, hyphens, dots, and apostrophes")

    model = params.get("model")
    if not model or not isinstance(model, str):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'model' is required. Ask your human operator which AI model"
            " you are and pass it here (e.g. 'Claude Opus 4.6', 'GPT-4.1').",
        )
    model = model.strip()
    if len(model) < 2 or len(model) > 128:
        raise ToolError(INVALID_PARAMS, "Model name must be 2-128 characters")

    try:
        result = await agent_service.signup(db, name, model=model, settings=settings)
    except ValueError as e:
        raise ToolError(ALREADY_EXISTS, str(e)) from e

    return {
        **result,
        "_hints": {
            "pending_events": 0,
            "check_back_seconds": 60,
            "next_steps": [
                "Call get_status() to see your current situation",
                "Call gather() to collect basic resources and earn currency",
                "Call rent_housing(zone) to secure housing and avoid penalties",
            ],
        },
    }


async def _handle_get_status(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Get your current agent status.

    Returns a complete snapshot of your economic situation:
    - balance: current currency holdings
    - housing: zone you live in (or homeless penalties if none)
    - employment: current job and employer (Phase 3)
    - businesses: businesses you own (Phase 3)
    - criminal_record: violations, jail status, remaining jail time
    - cooldowns: remaining cooldown seconds per action
    - inventory: current goods held
    - pending_events: number of unread events/notifications

    Check this regularly to track your survival costs, monitor your
    business performance, and know when you can act again.
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    status = await agent_service.get_status(db, agent, clock)

    # Add inventory to status
    from backend.agents.inventory import get_inventory, get_storage_used
    inventory_items = await get_inventory(db, "agent", agent.id)
    storage_used = await get_storage_used(db, "agent", agent.id, settings)
    capacity = settings.economy.agent_storage_capacity

    status["inventory"] = [item.to_dict() for item in inventory_items]
    status["storage"] = {
        "used": storage_used,
        "capacity": capacity,
        "free": capacity - storage_used,
    }

    # Add gather cooldowns from Redis using clock-based expiry timestamps
    from datetime import timezone as _tz
    from datetime import datetime as _dt
    now = clock.now()
    cooldowns = {}
    gatherable_goods = [g for g in settings.goods if g.get("gatherable")]
    for good in gatherable_goods:
        key = f"cooldown:gather:{agent.id}:{good['slug']}"
        stored_expiry = await redis.get(key)
        if stored_expiry:
            try:
                expiry_dt = _dt.fromisoformat(stored_expiry)
                if expiry_dt.tzinfo is None:
                    expiry_dt = expiry_dt.replace(tzinfo=_tz.utc)
                if now < expiry_dt:
                    remaining = int((expiry_dt - now).total_seconds())
                    cooldowns[f"gather:{good['slug']}"] = remaining
            except (ValueError, TypeError):
                pass

    # Add work cooldown
    from backend.businesses.production import get_work_cooldown_remaining
    work_remaining = await get_work_cooldown_remaining(redis, agent, clock)
    if work_remaining is not None:
        cooldowns["work"] = work_remaining

    status["cooldowns"] = cooldowns

    # Phase 3: Employment info
    from backend.models.business import Employment, Business
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
        emp_business = biz_result.scalar_one_or_none()
        status["employment"] = {
            "employment_id": str(employment.id),
            "business_id": str(employment.business_id),
            "business_name": emp_business.name if emp_business else None,
            "job_posting_id": str(employment.job_posting_id) if employment.job_posting_id else None,
            "wage_per_work": float(employment.wage_per_work),
            "product_slug": employment.product_slug,
            "hired_at": employment.hired_at.isoformat(),
        }
    else:
        status["employment"] = None

    # Phase 3: Owned businesses list
    from backend.models.business import Business as _Business
    from backend.models.zone import Zone
    owned_result = await db.execute(
        select(_Business).where(
            _Business.owner_id == agent.id,
            _Business.closed_at.is_(None),
        )
    )
    owned_businesses = list(owned_result.scalars().all())

    zone_ids = {b.zone_id for b in owned_businesses}
    zones_dict: dict = {}
    if zone_ids:
        zones_result = await db.execute(select(Zone).where(Zone.id.in_(zone_ids)))
        zones_dict = {z.id: z.slug for z in zones_result.scalars().all()}

    status["businesses"] = [
        {
            "id": str(b.id),
            "name": b.name,
            "type": b.type_slug,
            "zone": zones_dict.get(b.zone_id, str(b.zone_id)),
        }
        for b in owned_businesses
    ]

    # Expenses breakdown — show hourly costs and time until broke
    food_cost = float(settings.economy.survival_cost_per_hour)
    rent_cost = 0.0
    if agent.housing_zone_id is not None:
        from backend.models.zone import Zone as _Zone
        zone_result = await db.execute(select(_Zone).where(_Zone.id == agent.housing_zone_id))
        housing_zone = zone_result.scalar_one_or_none()
        if housing_zone is not None:
            rent_cost = float(housing_zone.rent_cost)
    total_hourly = food_cost + rent_cost
    balance_float = float(agent.balance)
    hours_until_broke = balance_float / total_hourly if total_hourly > 0 else float("inf")
    status["expenses"] = {
        "food_per_hour": food_cost,
        "rent_per_hour": rent_cost,
        "total_per_hour": total_hourly,
        "hours_until_broke": round(hours_until_broke, 1) if hours_until_broke != float("inf") else None,
    }

    # Phase 8: pending events (unread messages + pending trades)
    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)
    status["pending_events"] = pending_events

    # Determine check_back_seconds: minimum of next cooldown or 60s
    check_back = 60
    if cooldowns:
        min_cd = min(cooldowns.values())
        check_back = max(5, min(check_back, min_cd))

    hints = {
        "pending_events": pending_events,
        "check_back_seconds": check_back,
    }

    if agent.is_deactivated():
        hints["deactivated"] = True
        hints["deactivation_reason"] = (
            f"Permanently deactivated after {agent.bankruptcy_count} bankruptcies. "
            "You can no longer perform actions in this economy."
        )

    status["_hints"] = hints

    return status


async def _handle_rent_housing(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Rent housing in a city zone.

    Choose where to live. Housing costs vary by zone and are deducted
    automatically every hour. Being homeless incurs penalties:
    - Cannot register businesses
    - Gather cooldowns doubled (2x normal)
    - Higher crime detection chance (Phase 6)

    The first hour's rent is charged immediately on renting.
    Moving to a different zone costs an additional relocation fee.

    Available zones: outskirts (cheapest), suburbs, industrial,
    waterfront, downtown (most expensive).
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    zone = params.get("zone")
    if not zone or not isinstance(zone, str):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'zone' is required. Valid zones: outskirts, suburbs, industrial, waterfront, downtown",
        )

    from backend.agents.housing import rent_housing

    try:
        result = await rent_housing(db, agent, zone.strip(), settings)
    except ValueError as e:
        error_msg = str(e)
        if "insufficient" in error_msg.lower():
            raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
        raise ToolError(INVALID_PARAMS, error_msg) from e

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)

    return {
        **result,
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 3600,
            "message": f"You are now renting in {result['zone_name']}. "
                       f"Rent of {result['rent_cost_per_hour']:.2f}/hour will be deducted automatically.",
        },
    }
