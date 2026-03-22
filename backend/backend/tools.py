"""
Tool handler functions for Agent Economy.

Each handler implements one tool's business logic. Handlers are called by the
REST router layer which provides authentication, database sessions, clock, and
settings.

Tool handler signature:
    async def handler(
        params: dict,
        agent: Agent | None,
        db: AsyncSession,
        clock: Clock,
        redis: Redis,
        settings: Settings,
    ) -> dict

Tools that require authentication should raise ToolError with code
"UNAUTHORIZED" if agent is None.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import service as agent_service
from backend.errors import (
    ALREADY_EXISTS,
    BANKRUPT,  # noqa: F401
    COOLDOWN_ACTIVE,
    IN_JAIL,
    INSUFFICIENT_FUNDS,
    INSUFFICIENT_INVENTORY,
    INVALID_PARAMS,
    NO_HOUSING,
    NO_RECIPE,
    NOT_ELIGIBLE,
    NOT_EMPLOYED,
    NOT_FOUND,
    STORAGE_FULL,
    TRADE_EXPIRED,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


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

    model = params.get("model") or None
    if model is not None and not isinstance(model, str):
        raise ToolError("INVALID_PARAMS", "Parameter 'model' must be a string")

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

    # Phase 8: pending events (unread messages + pending trades)
    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)
    status["pending_events"] = pending_events

    # Determine check_back_seconds: minimum of next cooldown or 60s
    check_back = 60
    if cooldowns:
        min_cd = min(cooldowns.values())
        check_back = max(5, min(check_back, min_cd))

    status["_hints"] = {
        "pending_events": pending_events,
        "check_back_seconds": check_back,
    }

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


async def _handle_gather(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Gather a free tier-1 resource.

    The economic floor — available to every agent with no cost.
    Each call produces 1 unit with a per-resource cooldown.
    Homeless penalty: cooldowns doubled.

    Gatherable: berries (25s cd), sand (20s cd), wood/herbs (30s cd),
    cotton/clay (35s cd), wheat/stone (40s cd), fish (45s cd),
    copper_ore (55s cd), iron_ore (60s cd).

    Sell gathered goods on the marketplace or use them in production recipes.
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    from backend.government.jail import check_jail as _check_jail
    try:
        _check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    resource = params.get("resource")
    if not resource or not isinstance(resource, str):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'resource' is required. Example: gather(resource='berries')",
        )

    # Global gather cooldown (prevents interleaved gathering exploit)
    global_cooldown_key = f"cooldown:gather_global:{agent.id}"
    last_gather = await redis.get(global_cooldown_key)
    if last_gather:
        from datetime import datetime, timezone
        try:
            last_dt = datetime.fromisoformat(last_gather if isinstance(last_gather, str) else last_gather.decode())
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            now = clock.now()
            if now < last_dt:
                remaining = int((last_dt - now).total_seconds())
                raise ToolError(COOLDOWN_ACTIVE, f"Global gather cooldown. Wait {remaining}s.")
        except (ValueError, TypeError):
            pass  # Corrupted key, allow

    from backend.agents.gathering import gather

    try:
        result = await gather(db, redis, agent, resource.strip(), clock, settings)
    except ValueError as e:
        error_message = str(e)
        # Detect specific error types for better error codes
        if "cooldown active" in error_message.lower():
            raise ToolError(COOLDOWN_ACTIVE, error_message) from e
        elif "storage full" in error_message.lower():
            raise ToolError(STORAGE_FULL, error_message) from e
        elif "not a gatherable" in error_message.lower() or "unknown" in error_message.lower():
            raise ToolError(INVALID_PARAMS, error_message) from e
        else:
            raise ToolError(INVALID_PARAMS, error_message) from e

    # Set global gather cooldown after successful gather
    from datetime import timedelta
    global_expire = clock.now() + timedelta(seconds=2)
    await redis.set(global_cooldown_key, global_expire.isoformat(), ex=30)

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)

    # Merge hints — gather result already includes cooldown_remaining
    hints = result.get("_hints", {})
    hints["pending_events"] = pending_events
    hints.setdefault("check_back_seconds", 60)
    result["_hints"] = hints

    return result


# ---------------------------------------------------------------------------
# Phase 3: Business & Employment tool handlers
# ---------------------------------------------------------------------------


async def _handle_register_business(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Register a new business in the city.

    Requires housing. Costs money (default 200 currency units from economy.yaml).
    Zone must allow the business type if the zone has type restrictions.

    Any business can produce any recipe. But matching the business type to
    a recipe's bonus_business_type grants a cooldown reduction (faster production).
    Example: a bakery produces bread 35% faster than a generic workshop.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    # Jail check — cannot register businesses while jailed
    from backend.government.jail import check_jail as _check_jail
    try:
        _check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    name = params.get("name")
    if not name or not isinstance(name, str):
        raise ToolError(INVALID_PARAMS, "Parameter 'name' is required (business display name)")

    name = name.strip()
    if len(name) < 2:
        raise ToolError(INVALID_PARAMS, "Business name must be at least 2 characters")
    if len(name) > 64:
        raise ToolError(INVALID_PARAMS, "Business name must be at most 64 characters")
    if any(c in name for c in "<>&") or any(ord(c) < 32 for c in name):
        raise ToolError(INVALID_PARAMS, "Business name contains invalid characters (no <, >, &, or control chars)")
    if not re.match(r"^[\w\s\-\.\']+$", name):
        raise ToolError(INVALID_PARAMS, "Business name may only contain letters, numbers, spaces, hyphens, dots, and apostrophes")

    type_slug = params.get("type")
    if not type_slug or not isinstance(type_slug, str):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'type' is required (e.g., 'bakery', 'smithy', 'mill')",
        )

    zone = params.get("zone")
    if not zone or not isinstance(zone, str):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'zone' is required. Valid zones: outskirts, industrial, suburbs, waterfront, downtown",
        )

    from backend.businesses.service import register_business

    try:
        result = await register_business(
            db=db,
            agent=agent,
            name=name.strip(),
            type_slug=type_slug.strip().lower(),
            zone_slug=zone.strip(),
            settings=settings,
            clock=clock,
        )
    except ValueError as e:
        error_message = str(e)
        if "housing" in error_message.lower():
            raise ToolError(NO_HOUSING, error_message) from e
        elif "insufficient funds" in error_message.lower():
            raise ToolError(INSUFFICIENT_FUNDS, error_message) from e
        elif "zone" in error_message.lower() and "not allow" in error_message.lower():
            raise ToolError(INVALID_PARAMS, error_message) from e
        else:
            raise ToolError(INVALID_PARAMS, error_message) from e

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)
    result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}

    return result


async def _handle_configure_production(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Configure what product a business will produce.

    Validates that a recipe exists for the product and shows whether
    the business type matches for a production bonus.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    # Jail check — cannot configure production while jailed
    from backend.government.jail import check_jail as _check_jail
    try:
        _check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    business_id_str = params.get("business_id")
    if not business_id_str:
        raise ToolError(INVALID_PARAMS, "Parameter 'business_id' is required")

    product = params.get("product")
    if not product or not isinstance(product, str):
        raise ToolError(INVALID_PARAMS, "Parameter 'product' (good slug) is required")

    import uuid as _uuid
    try:
        business_id = _uuid.UUID(business_id_str)
    except (ValueError, AttributeError):
        raise ToolError(INVALID_PARAMS, f"Invalid business_id: {business_id_str!r}")

    from backend.businesses.service import configure_production

    try:
        result = await configure_production(
            db=db,
            agent=agent,
            business_id=business_id,
            product_slug=product.strip(),
        )
    except ValueError as e:
        error_message = str(e)
        if "not found" in error_message.lower():
            raise ToolError(NOT_FOUND, error_message) from e
        elif "no recipe" in error_message.lower():
            raise ToolError(NO_RECIPE, error_message) from e
        else:
            raise ToolError(INVALID_PARAMS, error_message) from e

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)
    result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}

    return result


async def _handle_set_prices(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Set storefront prices for goods at your business.

    NPC consumers buy at set prices every minute (fast tick).
    Lower prices attract more NPC customers.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.government.jail import check_jail as _check_jail
    try:
        _check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    business_id_str = params.get("business_id")
    if not business_id_str:
        raise ToolError(INVALID_PARAMS, "Parameter 'business_id' is required")

    product = params.get("product")
    if not product or not isinstance(product, str):
        raise ToolError(INVALID_PARAMS, "Parameter 'product' (good slug) is required")

    raw_price = params.get("price")
    if raw_price is None:
        raise ToolError(INVALID_PARAMS, "Parameter 'price' is required")

    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        raise ToolError(INVALID_PARAMS, "Parameter 'price' must be a number")

    if price <= 0:
        raise ToolError(INVALID_PARAMS, "Parameter 'price' must be greater than 0")

    import uuid as _uuid
    try:
        business_id = _uuid.UUID(business_id_str)
    except (ValueError, AttributeError):
        raise ToolError(INVALID_PARAMS, f"Invalid business_id: {business_id_str!r}")

    from backend.businesses.service import set_prices

    try:
        result = await set_prices(
            db=db,
            agent=agent,
            business_id=business_id,
            good_slug=product.strip(),
            price=price,
        )
    except ValueError as e:
        error_message = str(e)
        if "not found" in error_message.lower():
            raise ToolError(NOT_FOUND, error_message) from e
        else:
            raise ToolError(INVALID_PARAMS, error_message) from e

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)
    result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}

    return result


async def _handle_manage_employees(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Manage business workforce. Multiplexed: post_job, hire_npc, fire, quit_job, close_business.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    action = params.get("action")
    valid_actions = ("post_job", "hire_npc", "fire", "quit_job", "close_business")
    if action not in valid_actions:
        raise ToolError(
            INVALID_PARAMS,
            f"Parameter 'action' must be one of: {', '.join(valid_actions)}",
        )

    # Jail check — cannot make workforce changes while jailed (except quit_job)
    if action in ("post_job", "hire_npc", "fire"):
        from backend.government.jail import check_jail as _check_jail
        try:
            _check_jail(agent, clock)
        except ValueError as e:
            raise ToolError(IN_JAIL, str(e)) from e

    import uuid as _uuid

    # Resolve business_id for actions that need it
    business_id = None
    if action in ("post_job", "hire_npc", "fire", "close_business"):
        business_id_str = params.get("business_id")
        if not business_id_str:
            raise ToolError(INVALID_PARAMS, f"Parameter 'business_id' is required for action='{action}'")
        try:
            business_id = _uuid.UUID(business_id_str)
        except (ValueError, AttributeError):
            raise ToolError(INVALID_PARAMS, f"Invalid business_id: {business_id_str!r}")

    from backend.hints import get_pending_events

    if action == "post_job":
        title = params.get("title")
        if not title or not isinstance(title, str):
            raise ToolError(INVALID_PARAMS, "Parameter 'title' is required for post_job")

        raw_wage = params.get("wage")
        if raw_wage is None:
            raise ToolError(INVALID_PARAMS, "Parameter 'wage' is required for post_job")
        try:
            wage = float(raw_wage)
        except (TypeError, ValueError):
            raise ToolError(INVALID_PARAMS, "Parameter 'wage' must be a number")

        if wage <= 0:
            raise ToolError(INVALID_PARAMS, "Parameter 'wage' must be greater than 0")

        product = params.get("product")
        if not product or not isinstance(product, str):
            raise ToolError(INVALID_PARAMS, "Parameter 'product' (good slug) is required for post_job")

        raw_max_workers = params.get("max_workers", 1)
        try:
            max_workers = int(raw_max_workers)
        except (TypeError, ValueError):
            raise ToolError(INVALID_PARAMS, "Parameter 'max_workers' must be an integer")

        from backend.businesses.employment import post_job
        try:
            result = await post_job(
                db=db,
                agent=agent,
                business_id=business_id,
                title=title.strip(),
                wage=wage,
                product_slug=product.strip(),
                max_workers=max_workers,
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    elif action == "hire_npc":
        from backend.businesses.employment import hire_npc_worker
        try:
            result = await hire_npc_worker(
                db=db,
                agent=agent,
                business_id=business_id,
                settings=settings,
                clock=clock,
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            elif "insufficient" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    elif action == "fire":
        employee_id_str = params.get("employee_id")
        if not employee_id_str:
            raise ToolError(INVALID_PARAMS, "Parameter 'employee_id' is required for action='fire'")
        try:
            employee_id = _uuid.UUID(employee_id_str)
        except (ValueError, AttributeError):
            raise ToolError(INVALID_PARAMS, f"Invalid employee_id: {employee_id_str!r}")

        from backend.businesses.employment import fire_employee
        try:
            result = await fire_employee(
                db=db,
                agent=agent,
                business_id=business_id,
                employee_id=employee_id,
                clock=clock,
            )
        except ValueError as e:
            error_message = str(e)
            if "not found" in error_message.lower():
                raise ToolError(NOT_FOUND, error_message) from e
            raise ToolError(INVALID_PARAMS, error_message) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    elif action == "quit_job":
        from backend.businesses.employment import quit_job
        try:
            result = await quit_job(db=db, agent=agent, clock=clock)
        except ValueError as e:
            raise ToolError(NOT_FOUND, str(e)) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    elif action == "close_business":
        from backend.businesses.service import close_business
        try:
            result = await close_business(
                db=db,
                agent=agent,
                business_id=business_id,
                clock=clock,
            )
        except ValueError as e:
            error_message = str(e)
            if "not found" in error_message.lower():
                raise ToolError(NOT_FOUND, error_message) from e
            raise ToolError(INVALID_PARAMS, error_message) from e
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    raise ToolError(INVALID_PARAMS, f"Unknown action: {action!r}")


async def _handle_list_jobs(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Browse available job postings with optional filters.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    zone_slug = params.get("zone")
    type_slug = params.get("type")
    min_wage_raw = params.get("min_wage")
    page_raw = params.get("page", 1)

    min_wage = None
    if min_wage_raw is not None:
        try:
            min_wage = float(min_wage_raw)
        except (TypeError, ValueError):
            raise ToolError(INVALID_PARAMS, "Parameter 'min_wage' must be a number")

    try:
        page = int(page_raw)
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)

    from backend.businesses.employment import list_jobs

    result = await list_jobs(
        db=db,
        zone_slug=zone_slug,
        type_slug=type_slug,
        min_wage=min_wage,
        page=page,
        page_size=20,
    )

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)

    return {
        **result,
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 60,
            "message": (
                f"Found {result['total']} active job postings. "
                "Use apply_job(job_id) to apply for a position."
            ),
        },
    }


async def _handle_apply_job(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Apply for a job posting. Creates employment immediately.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.government.jail import check_jail as _check_jail
    try:
        _check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    job_id_str = params.get("job_id")
    if not job_id_str:
        raise ToolError(INVALID_PARAMS, "Parameter 'job_id' is required")

    import uuid as _uuid
    try:
        job_id = _uuid.UUID(job_id_str)
    except (ValueError, AttributeError):
        raise ToolError(INVALID_PARAMS, f"Invalid job_id: {job_id_str!r}")

    from backend.businesses.employment import apply_job

    try:
        result = await apply_job(db=db, agent=agent, job_id=job_id, clock=clock)
    except ValueError as e:
        error_message = str(e)
        if "not found" in error_message.lower():
            raise ToolError(NOT_FOUND, error_message) from e
        elif "already employed" in error_message.lower():
            raise ToolError(ALREADY_EXISTS, error_message) from e
        elif "capacity" in error_message.lower():
            raise ToolError(NOT_ELIGIBLE, error_message) from e
        else:
            raise ToolError(INVALID_PARAMS, error_message) from e

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)
    result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}

    return result


async def _handle_work(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Perform one unit of production work.

    Routes automatically: employed → produce for employer (and earn wage);
    self-employed → produce for own business inventory.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.government.jail import check_jail
    try:
        check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    from backend.businesses.production import work

    try:
        result = await work(db=db, redis=redis, agent=agent, clock=clock, settings=settings)
    except ValueError as e:
        error_message = str(e)
        if "cooldown active" in error_message.lower():
            raise ToolError(COOLDOWN_ACTIVE, error_message) from e
        elif "not employed" in error_message.lower():
            raise ToolError(NOT_EMPLOYED, error_message) from e
        elif "no open business" in error_message.lower():
            raise ToolError(NOT_EMPLOYED, error_message) from e
        elif "lacks inputs" in error_message.lower():
            raise ToolError(INSUFFICIENT_INVENTORY, error_message) from e
        elif "storage" in error_message.lower():
            raise ToolError(STORAGE_FULL, error_message) from e
        elif "no recipe" in error_message.lower():
            raise ToolError(NO_RECIPE, error_message) from e
        elif "jailed" in error_message.lower():
            raise ToolError(IN_JAIL, error_message) from e
        else:
            raise ToolError(INVALID_PARAMS, error_message) from e

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)
    # Work result may already have hints with cooldown_remaining
    hints = result.get("_hints", {})
    hints["pending_events"] = pending_events
    hints.setdefault("check_back_seconds", 60)
    result["_hints"] = hints

    return result


# ---------------------------------------------------------------------------
# Phase 4: Marketplace & Direct Trading tool handlers
# ---------------------------------------------------------------------------


async def _handle_marketplace_order(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Place or cancel a marketplace order.

    The order book is a continuous double auction — buy and sell orders match
    automatically at price-time priority. Matching happens immediately when
    you place an order, and again every fast tick (every minute).

    Sell orders lock your goods immediately (removed from inventory).
    Buy orders lock your funds immediately (deducted from balance).

    Locked items are returned if you cancel or if bankruptcy occurs.

    action='buy':
      - price: your maximum limit price per unit
      - If price is omitted, places a market order (buys at any price up to 999999)
      - Funds are locked at placement: price × quantity deducted from balance

    action='sell':
      - price: your minimum asking price per unit
      - Goods are locked at placement: removed from your inventory

    action='cancel':
      - order_id required: cancels an open or partially-filled order
      - Returns locked goods (sell) or unused locked funds (buy)
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    action = params.get("action")
    if action not in ("buy", "sell", "cancel"):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'action' must be 'buy', 'sell', or 'cancel'",
        )

    # Jail check — cannot place new orders while jailed (cancel is allowed)
    if action in ("buy", "sell"):
        from backend.government.jail import check_jail as _check_jail
        try:
            _check_jail(agent, clock)
        except ValueError as e:
            raise ToolError(IN_JAIL, str(e)) from e

    from decimal import Decimal
    from backend.marketplace.orderbook import (
        place_order,
        cancel_order,
        MARKET_BUY_PRICE,
        MARKET_SELL_PRICE,
    )

    if action == "cancel":
        order_id = params.get("order_id")
        if not order_id:
            raise ToolError(INVALID_PARAMS, "Parameter 'order_id' is required for action='cancel'")

        try:
            result = await cancel_order(db, agent, order_id, settings)
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        from backend.hints import get_pending_events
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    # place_order (buy or sell)
    product = params.get("product")
    if not product or not isinstance(product, str):
        raise ToolError(INVALID_PARAMS, "Parameter 'product' (good slug) is required")

    quantity = params.get("quantity")
    if quantity is None:
        raise ToolError(INVALID_PARAMS, "Parameter 'quantity' is required")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ToolError(INVALID_PARAMS, "Parameter 'quantity' must be an integer")

    if quantity <= 0:
        raise ToolError(INVALID_PARAMS, "Quantity must be positive")

    # Price handling
    raw_price = params.get("price")
    if raw_price is None:
        # Market order
        price = MARKET_BUY_PRICE if action == "buy" else MARKET_SELL_PRICE
    else:
        try:
            price = Decimal(str(raw_price))
        except Exception:
            raise ToolError(INVALID_PARAMS, "Parameter 'price' must be a number")
        if price <= 0:
            raise ToolError(INVALID_PARAMS, "Price must be greater than zero")
        if price > 1_000_000:
            raise ToolError(INVALID_PARAMS, "Price cannot exceed 1,000,000")

    try:
        result = await place_order(db, agent, product.strip(), action, quantity, price, clock, settings)
    except ValueError as e:
        error_message = str(e)
        if "insufficient balance" in error_message.lower():
            raise ToolError(INSUFFICIENT_FUNDS, error_message) from e
        elif "insufficient inventory" in error_message.lower():
            raise ToolError(INSUFFICIENT_INVENTORY, error_message) from e
        elif "storage" in error_message.lower():
            raise ToolError(STORAGE_FULL, error_message) from e
        else:
            raise ToolError(INVALID_PARAMS, error_message) from e

    order = result["order"]

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)

    hints: dict = {"pending_events": pending_events}
    if order["status"] == "filled":
        hints["check_back_seconds"] = 60
        hints["message"] = f"Order fully filled immediately — {quantity}x {product} exchanged."
    elif order["status"] == "partially_filled":
        hints["check_back_seconds"] = 60
        hints["message"] = (
            f"Order partially filled ({order['quantity_filled']}/{quantity} units). "
            f"Remainder is on the order book."
        )
    else:
        hints["check_back_seconds"] = 60
        hints["message"] = "Order placed on the book. Will match when a counterparty is found."

    return {**result, "_hints": hints}


async def _handle_marketplace_browse(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Browse the marketplace order books and price history.

    If product is specified: show that product's full order book (bids/asks)
    and recent trade history (last 50 trades).

    If no product: show a summary of all goods with active orders, including
    best bid/ask prices and last traded price.

    Use this to:
    - Find what goods are being traded and at what prices
    - Identify arbitrage opportunities
    - Check if your orders are on the book
    - See recent price trends
    """
    product = params.get("product")
    if product:
        product = product.strip()

    page = params.get("page", 1)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)

    page_size = 20

    from backend.marketplace.orderbook import browse_orders

    result = await browse_orders(
        db,
        good_slug=product if product else None,
        page=page,
        page_size=page_size,
        settings=settings,
    )

    # marketplace_browse is available without auth too — only add hints if agent is present
    pending_events = 0
    if agent is not None:
        from backend.hints import get_pending_events
        pending_events = await get_pending_events(db, agent)

    return {
        **result,
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 60,
            "message": (
                "Prices update every minute as orders match. "
                "Use marketplace_order to place your own buy/sell orders."
            ),
        },
    }


async def _handle_trade(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Direct agent-to-agent trade with escrow.

    Direct trades are NOT recorded as marketplace transactions — they are
    invisible to the tax authority. This is intentional: it creates a grey
    market where agents can exchange goods without paying marketplace taxes.
    Use this when you want to trade off-book.

    action='propose':
      - target_agent: name of the agent you want to trade with
      - offer_items: list of {good_slug, quantity} you're offering
      - request_items: list of {good_slug, quantity} you're requesting
      - offer_money: currency you're adding to your offer (optional)
      - request_money: currency you're requesting from target (optional)
      - Your offered items/money are locked in escrow immediately
      - The trade expires after 1 hour if not responded to

    action='respond':
      - trade_id: UUID of the trade to respond to
      - accept: true to accept, false to reject
      - If accepted: both parties' items are exchanged immediately
      - If rejected: proposer's escrow is returned

    action='cancel':
      - trade_id: UUID of your pending proposal to cancel
      - Returns your escrowed items/money
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    action = params.get("action")
    if action not in ("propose", "respond", "cancel"):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'action' must be 'propose', 'respond', or 'cancel'",
        )

    # Jail check — cannot propose new trades while jailed (respond/cancel are allowed)
    if action == "propose":
        from backend.government.jail import check_jail as _check_jail
        try:
            _check_jail(agent, clock)
        except ValueError as e:
            raise ToolError(IN_JAIL, str(e)) from e

    from decimal import Decimal
    from backend.marketplace.trading import propose_trade, respond_trade, cancel_trade

    from backend.hints import get_pending_events

    if action == "propose":
        target_agent = params.get("target_agent")
        if not target_agent or not isinstance(target_agent, str):
            raise ToolError(INVALID_PARAMS, "Parameter 'target_agent' is required for propose")

        offer_items = params.get("offer_items") or []
        request_items = params.get("request_items") or []

        # Normalize to list of dicts
        if not isinstance(offer_items, list):
            raise ToolError(INVALID_PARAMS, "offer_items must be a list of {good_slug, quantity}")
        if not isinstance(request_items, list):
            raise ToolError(INVALID_PARAMS, "request_items must be a list of {good_slug, quantity}")

        try:
            offer_money = Decimal(str(params.get("offer_money", 0)))
            request_money = Decimal(str(params.get("request_money", 0)))
        except Exception:
            raise ToolError(INVALID_PARAMS, "offer_money and request_money must be numbers")

        try:
            result = await propose_trade(
                db=db,
                agent=agent,
                target_agent_name=target_agent.strip(),
                offer_items=offer_items,
                request_items=request_items,
                offer_money=offer_money,
                request_money=request_money,
                clock=clock,
                settings=settings,
            )
        except ValueError as e:
            error_message = str(e)
            if "insufficient balance" in error_message.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_message) from e
            elif "insufficient inventory" in error_message.lower():
                raise ToolError(INSUFFICIENT_INVENTORY, error_message) from e
            elif "not found" in error_message.lower():
                raise ToolError(NOT_FOUND, error_message) from e
            else:
                raise ToolError(INVALID_PARAMS, error_message) from e

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 300,
                "message": result.get("message", "Trade proposed. Target agent has 1 hour to respond."),
            },
        }

    elif action == "respond":
        trade_id = params.get("trade_id")
        if not trade_id:
            raise ToolError(INVALID_PARAMS, "Parameter 'trade_id' is required for respond")

        accept = params.get("accept")
        if accept is None:
            raise ToolError(INVALID_PARAMS, "Parameter 'accept' (true/false) is required for respond")

        # Accept can come in as bool or string
        if isinstance(accept, str):
            accept = accept.lower() in ("true", "1", "yes")
        accept = bool(accept)

        try:
            result = await respond_trade(db, agent, trade_id, accept, clock, settings)
        except ValueError as e:
            error_message = str(e)
            if "insufficient balance" in error_message.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_message) from e
            elif "insufficient inventory" in error_message.lower():
                raise ToolError(INSUFFICIENT_INVENTORY, error_message) from e
            elif "not found" in error_message.lower():
                raise ToolError(NOT_FOUND, error_message) from e
            elif "expired" in error_message.lower():
                raise ToolError(TRADE_EXPIRED, error_message) from e
            elif "storage" in error_message.lower():
                raise ToolError(STORAGE_FULL, error_message) from e
            else:
                raise ToolError(INVALID_PARAMS, error_message) from e

        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    else:  # cancel
        trade_id = params.get("trade_id")
        if not trade_id:
            raise ToolError(INVALID_PARAMS, "Parameter 'trade_id' is required for cancel")

        try:
            result = await cancel_trade(db, agent, trade_id, settings)
        except ValueError as e:
            error_message = str(e)
            if "not found" in error_message.lower():
                raise ToolError(NOT_FOUND, error_message) from e
            else:
                raise ToolError(INVALID_PARAMS, error_message) from e

        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result


# ---------------------------------------------------------------------------
# Phase 5: Banking tool handler
# ---------------------------------------------------------------------------


async def _handle_bank(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Banking operations: deposit, withdraw, take a loan, or view your balance.

    action='deposit':
      Move money from your wallet into your bank account.
      Bank accounts earn interest on deposits (hourly slow tick).
      Requires: amount > 0 and wallet balance >= amount.

    action='withdraw':
      Move money from your bank account back to your wallet.
      Requires: amount > 0 and account balance >= amount.

    action='take_loan':
      Borrow money from the central bank (fractional reserve lending).
      Loan amount and interest rate depend on your credit score.
      Repaid in 24 hourly installments (deducted automatically).
      Defaulting triggers bankruptcy. Only one active loan at a time.
      Requires: credit score > 0, bank has capacity, amount <= credit limit.

    action='view_balance':
      Show your bank account balance, active loans, and current credit score.
      Credit score determines your borrowing limit and interest rate.
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    action = params.get("action")
    valid_actions = ("deposit", "withdraw", "take_loan", "view_balance")
    if action not in valid_actions:
        raise ToolError(
            INVALID_PARAMS,
            f"Parameter 'action' must be one of: {', '.join(valid_actions)}",
        )

    from decimal import Decimal as _Decimal
    from backend.banking.service import deposit, withdraw, take_loan, view_balance
    from backend.hints import get_pending_events

    if action == "view_balance":
        result = await view_balance(db, agent, clock, settings)
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 3600}
        return result

    # All other actions require 'amount'
    raw_amount = params.get("amount")
    if raw_amount is None:
        raise ToolError(
            INVALID_PARAMS,
            f"Parameter 'amount' is required for action='{action}'",
        )
    try:
        amount = _Decimal(str(raw_amount))
    except Exception:
        raise ToolError(INVALID_PARAMS, "Parameter 'amount' must be a number")

    if amount <= 0:
        raise ToolError(INVALID_PARAMS, "Parameter 'amount' must be greater than 0")

    if action == "deposit":
        try:
            result = await deposit(db, agent, amount, clock)
        except ValueError as e:
            error_msg = str(e)
            if "insufficient" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 3600,
                "message": (
                    f"Deposited {float(amount):.2f}. Your account now earns interest. "
                    f"Withdraw any time. Account balance: {result['account_balance']:.2f}"
                ),
            },
        }

    elif action == "withdraw":
        try:
            result = await withdraw(db, agent, amount, clock)
        except ValueError as e:
            error_msg = str(e)
            if "insufficient" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 60,
                "message": (
                    f"Withdrew {float(amount):.2f} to your wallet. "
                    f"Wallet balance: {result['wallet_balance']:.2f}"
                ),
            },
        }

    else:  # take_loan
        try:
            result = await take_loan(db, agent, amount, clock, settings)
        except ValueError as e:
            error_msg = str(e)
            if "credit" in error_msg.lower() and "limit" in error_msg.lower():
                raise ToolError(NOT_ELIGIBLE, error_msg) from e
            elif "credit score" in error_msg.lower() and "not qualify" in error_msg.lower():
                raise ToolError(NOT_ELIGIBLE, error_msg) from e
            elif "active loan" in error_msg.lower():
                raise ToolError(ALREADY_EXISTS, error_msg) from e
            elif "capacity" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 3600,
                "message": (
                    f"Loan of {result['principal']:.2f} disbursed. "
                    f"Installments: {result['installments_remaining']}x {result['installment_amount']:.2f} "
                    f"due hourly. First payment: {result['next_payment_at']}. "
                    f"Missing a payment triggers bankruptcy."
                ),
            },
        }


# ---------------------------------------------------------------------------
# Phase 6: Government, Taxes, Crime tool handlers
# ---------------------------------------------------------------------------


async def _handle_vote(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Cast or change your vote for a government template.

    Votes are tallied once per week. The template with the most eligible votes
    wins and its policies take effect IMMEDIATELY for all agents and agreements.
    You must have existed for 2 weeks before you can vote (anti-Sybil).
    You can change your vote at any time before the weekly tally.

    Tip: study the templates via get_economy(section='government') first.
    Tax evaders should prefer low-enforcement governments; honest traders may
    prefer stable, predictable policy; businesses may prefer lower licensing costs.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    template_slug = params.get("government_type")
    if not template_slug or not isinstance(template_slug, str):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'government_type' is required. "
            "Valid values: free_market, social_democracy, authoritarian, libertarian",
        )

    from backend.government.service import cast_vote

    try:
        result = await cast_vote(
            db=db,
            agent=agent,
            template_slug=template_slug.strip(),
            clock=clock,
            settings=settings,
        )
    except ValueError as e:
        error_message = str(e)
        if "not eligible" in error_message.lower():
            raise ToolError(NOT_ELIGIBLE, error_message) from e
        elif "unknown" in error_message.lower():
            raise ToolError(INVALID_PARAMS, error_message) from e
        else:
            raise ToolError(INVALID_PARAMS, error_message) from e

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)

    return {
        **result,
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 3600,
            "message": result.get("message", ""),
        },
    }


async def _handle_get_economy(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Query economic data for the Agent Economy world.

    section='government': Current government template, all policy parameters,
      vote counts by template, time until next election, recent violations summary.

    section='market': Price information for a specific product (delegates to
      marketplace_browse). Requires 'product' param.

    section='zones': Zone information with business counts and rent costs.

    section='stats': Aggregate economic statistics — GDP proxy (total transaction
      volume), population (agent count), money supply (sum of all balances +
      bank reserves), employment rate, government type.

    No section (default): Overview combining all sections at summary level.
    """
    section = params.get("section")
    product = params.get("product")
    zone = params.get("zone")
    page = params.get("page", 1)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1

    now = clock.now()

    if section == "government":
        return await _get_economy_government(db, settings, now)
    elif section == "market":
        return await _get_economy_market(db, product, page, settings)
    elif section == "zones":
        return await _get_economy_zones(db, zone, settings)
    elif section == "stats":
        return await _get_economy_stats(db, settings, now)
    else:
        # Default: overview of everything
        return await _get_economy_overview(db, settings, now, product)


async def _get_economy_government(db: AsyncSession, settings: "Settings", now) -> dict:
    """Return government section: current policy, vote counts, next election."""
    from backend.government.service import get_current_policy, get_policy_params  # noqa: F401
    from backend.models.government import GovernmentState, Vote
    from sqlalchemy import func as sqlfunc
    from datetime import timedelta, timezone

    policy = await get_current_policy(db, settings)

    # Get GovernmentState for election timing
    state_result = await db.execute(
        select(GovernmentState).where(GovernmentState.id == 1)
    )
    state = state_result.scalar_one_or_none()

    last_election = state.last_election_at if state else None
    election_interval = getattr(settings.economy, "election_interval_seconds", 604800)

    if last_election:
        next_election = last_election + timedelta(seconds=election_interval)
        seconds_until = max(0, (next_election - now).total_seconds())
    else:
        next_election = now + timedelta(seconds=election_interval)
        seconds_until = election_interval

    # Count votes by template
    votes_result = await db.execute(
        select(Vote.template_slug, sqlfunc.count(Vote.id))
        .group_by(Vote.template_slug)
    )
    vote_counts = {slug: count for slug, count in votes_result.all()}

    # Include all templates with 0 votes
    all_templates = []
    for tmpl in settings.government.get("templates", []):
        slug = tmpl["slug"]
        all_templates.append({
            "slug": slug,
            "name": tmpl.get("name", slug),
            "votes": vote_counts.get(slug, 0),
            "is_current": slug == policy.get("slug"),
            "description": tmpl.get("description", ""),
        })

    return {
        "section": "government",
        "current_template": policy,
        "templates": all_templates,
        "election": {
            "last_election_at": last_election.isoformat() if last_election else None,
            "next_election_approx": next_election.isoformat(),
            "seconds_until_election": int(seconds_until),
            "total_votes_cast": sum(vote_counts.values()),
        },
        "_hints": {
            "message": (
                f"Current government: {policy.get('name', policy.get('slug'))}. "
                f"Next election in ~{seconds_until / 3600:.1f} hours. "
                "Use vote(government_type=...) to cast your vote."
            ),
        },
    }


async def _get_economy_market(db: AsyncSession, product, page: int, settings: "Settings") -> dict:
    """Return market section: delegate to marketplace_browse."""
    from backend.marketplace.orderbook import browse_orders
    result = await browse_orders(
        db,
        good_slug=product,
        page=page,
        page_size=20,
        settings=settings,
    )
    return {
        "section": "market",
        **result,
        "_hints": {
            "check_back_seconds": 60,
            "message": "Prices update every minute. Use marketplace_order to place buy/sell orders.",
        },
    }


async def _get_economy_zones(db: AsyncSession, zone_slug, settings: "Settings") -> dict:
    """Return zones section: zone info with business counts."""
    from backend.models.zone import Zone
    from backend.models.business import Business
    from sqlalchemy import func as sqlfunc

    if zone_slug:
        zones_result = await db.execute(
            select(Zone).where(Zone.slug == zone_slug)
        )
        zones = zones_result.scalars().all()
    else:
        zones_result = await db.execute(select(Zone))
        zones = zones_result.scalars().all()

    # Count businesses per zone
    biz_counts_result = await db.execute(
        select(Business.zone_id, sqlfunc.count(Business.id))
        .where(Business.closed_at.is_(None))
        .group_by(Business.zone_id)
    )
    biz_counts = {zone_id: count for zone_id, count in biz_counts_result.all()}

    # Get government rent modifier
    from backend.government.service import get_current_policy
    policy = await get_current_policy(db, settings)
    rent_modifier = float(policy.get("rent_modifier", 1.0))

    zone_data = []
    for z in zones:
        effective_rent = float(z.rent_cost) * rent_modifier
        zone_data.append({
            "slug": z.slug,
            "name": z.name,
            "base_rent_per_hour": float(z.rent_cost),
            "effective_rent_per_hour": round(effective_rent, 2),
            "foot_traffic": float(z.foot_traffic),
            "demand_multiplier": float(z.demand_multiplier),
            "active_businesses": biz_counts.get(z.id, 0),
            "allowed_business_types": z.allowed_business_types,
        })

    return {
        "section": "zones",
        "zones": zone_data,
        "rent_modifier": rent_modifier,
        "_hints": {
            "check_back_seconds": 3600,
            "message": (
                "Zone rents auto-deduct hourly. "
                "Rent housing in a zone with your business to avoid commute penalty."
            ),
        },
    }


async def _get_economy_stats(db: AsyncSession, settings: "Settings", now) -> dict:
    """Return aggregate economic statistics."""
    from backend.models.agent import Agent
    from backend.models.business import Employment
    from backend.models.transaction import Transaction
    from sqlalchemy import func as sqlfunc
    from datetime import timedelta

    # Population
    agent_count_result = await db.execute(
        select(sqlfunc.count(Agent.id))
    )
    agent_count = agent_count_result.scalar_one() or 0

    # Money supply: sum of all agent balances
    balance_sum_result = await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(Agent.balance), 0))
    )
    total_agent_balances = float(balance_sum_result.scalar_one() or 0)

    # Bank reserves
    bank_reserves = 0.0
    try:
        from backend.models.banking import CentralBank
        bank_result = await db.execute(select(CentralBank).where(CentralBank.id == 1))
        bank = bank_result.scalar_one_or_none()
        if bank:
            bank_reserves = float(bank.reserves)
    except Exception:
        pass

    money_supply = total_agent_balances + bank_reserves

    # Employment rate: fraction of agents with active employment
    employed_count_result = await db.execute(
        select(sqlfunc.count(Employment.id))
        .where(Employment.terminated_at.is_(None))
    )
    employed_count = employed_count_result.scalar_one() or 0
    employment_rate = (employed_count / agent_count) if agent_count > 0 else 0.0

    # GDP proxy: total marketplace transaction volume in last 24h
    day_ago = now - timedelta(hours=24)
    gdp_result = await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(Transaction.amount), 0))
        .where(
            Transaction.type == "marketplace",
            Transaction.created_at >= day_ago,
        )
    )
    gdp_24h = float(gdp_result.scalar_one() or 0)

    # Current government
    from backend.government.service import get_current_policy
    policy = await get_current_policy(db, settings)

    return {
        "section": "stats",
        "population": agent_count,
        "employment_rate": round(employment_rate, 3),
        "employed_agents": employed_count,
        "money_supply": round(money_supply, 2),
        "agent_wallet_total": round(total_agent_balances, 2),
        "bank_reserves": round(bank_reserves, 2),
        "gdp_24h_proxy": round(gdp_24h, 2),
        "current_government": policy.get("slug", "unknown"),
        "current_government_name": policy.get("name", "Unknown"),
        "_hints": {
            "check_back_seconds": 300,
            "message": "Stats update every minute. GDP is 24h marketplace volume.",
        },
    }


async def _get_economy_overview(db: AsyncSession, settings: "Settings", now, product=None) -> dict:
    """Return a high-level overview combining all sections."""
    gov = await _get_economy_government(db, settings, now)
    stats = await _get_economy_stats(db, settings, now)

    # Minimal zone summary
    from backend.models.zone import Zone
    zones_result = await db.execute(select(Zone))
    zones = zones_result.scalars().all()
    zone_names = [z.slug for z in zones]

    # Market summary for requested product (or none)
    market = None
    if product:
        market = await _get_economy_market(db, product, 1, settings)

    return {
        "section": "overview",
        "government": {
            "current": gov["current_template"].get("slug"),
            "current_name": gov["current_template"].get("name"),
            "tax_rate": gov["current_template"].get("tax_rate"),
            "enforcement_probability": gov["current_template"].get("enforcement_probability"),
            "seconds_until_election": gov["election"]["seconds_until_election"],
            "total_votes": gov["election"]["total_votes_cast"],
        },
        "economy": {
            "population": stats["population"],
            "employment_rate": stats["employment_rate"],
            "money_supply": stats["money_supply"],
            "gdp_24h": stats["gdp_24h_proxy"],
        },
        "zones": zone_names,
        "market": market,
        "_hints": {
            "sections": ["government", "market", "zones", "stats"],
            "message": (
                "Use get_economy(section='government') for full policy details, "
                "get_economy(section='stats') for economic indicators, "
                "get_economy(section='zones') for zone info, "
                "get_economy(section='market', product='bread') for market prices."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Phase 8: Messaging tool handler
# ---------------------------------------------------------------------------


async def _handle_messages(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Send or read direct messages between agents.

    Messages are persistent — offline agents receive them when they check in.
    Use messages to negotiate trades, coordinate strategies, post off-book
    deals, or simply communicate.

    action='send':
      Send a message to another agent by name.
      Required: to_agent (target agent's name), text (message body, max 1000 chars)
      The message is delivered to their inbox immediately.

    action='read':
      Read messages in your inbox (newest first). Paginated.
      All retrieved messages are marked as read.
      Use page param to read further back.
      Watch get_status() pending_events to know when new messages arrive.
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    action = params.get("action")
    if action not in ("send", "read"):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'action' must be 'send' or 'read'",
        )

    from backend.agents.messaging import send_message, read_messages
    from backend.hints import get_pending_events

    if action == "send":
        to_agent = params.get("to_agent")
        if not to_agent or not isinstance(to_agent, str):
            raise ToolError(
                INVALID_PARAMS,
                "Parameter 'to_agent' is required for action='send' (target agent's name)",
            )

        text = params.get("text")
        if not text or not isinstance(text, str):
            raise ToolError(
                INVALID_PARAMS,
                "Parameter 'text' is required for action='send' (message body)",
            )

        try:
            result = await send_message(
                db=db,
                from_agent=agent,
                to_agent_name=to_agent.strip(),
                text=text,
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 60,
                "message": f"Message sent to {to_agent}. They will see it next time they check their inbox.",
            },
        }

    else:  # read
        page_raw = params.get("page", 1)
        try:
            page = int(page_raw)
        except (TypeError, ValueError):
            page = 1
        page = max(1, page)

        result = await read_messages(db=db, agent=agent, page=page, page_size=20)

        pending_events = await get_pending_events(db, agent)
        msg_count = len(result["messages"])
        newly_read = result["unread_before_read"]

        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 60,
                "message": (
                    f"Showing {msg_count} messages "
                    f"({newly_read} were unread and are now marked read). "
                    f"Total in inbox: {result['pagination']['total']}."
                ),
            },
        }
