"""Business registration and configuration handlers."""

from __future__ import annotations

import re
import uuid as _uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from backend.errors import (
    IN_JAIL,
    INSUFFICIENT_FUNDS,
    INVALID_PARAMS,
    NO_HOUSING,
    NO_RECIPE,
    NOT_FOUND,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_register_business(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
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

    from backend.government.jail import check_jail

    try:
        check_jail(agent, clock)
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
        raise ToolError(
            INVALID_PARAMS, "Business name may only contain letters, numbers, spaces, hyphens, dots, and apostrophes"
        )

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

    # Business type and zone slug validated downstream: registration.register_business
    # raises ValueError if the zone doesn't exist or doesn't allow this business type.
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
        error_msg = str(e)
        if "housing" in error_msg.lower():
            raise ToolError(NO_HOUSING, error_msg) from e
        elif "insufficient funds" in error_msg.lower():
            raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
        elif "zone" in error_msg.lower() and "not allow" in error_msg.lower():
            raise ToolError(INVALID_PARAMS, error_msg) from e
        else:
            raise ToolError(INVALID_PARAMS, error_msg) from e

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)
    result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}

    return result


async def _handle_configure_production(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Configure what product a business will produce.

    Validates that a recipe exists for the product and shows whether
    the business type matches for a production bonus.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.government.jail import check_jail

    try:
        check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    business_id_str = params.get("business_id")
    if not business_id_str:
        raise ToolError(INVALID_PARAMS, "Parameter 'business_id' is required")

    product = params.get("product")
    if not product or not isinstance(product, str):
        raise ToolError(INVALID_PARAMS, "Parameter 'product' (good slug) is required")

    try:
        business_id = _uuid.UUID(business_id_str)
    except ValueError, AttributeError:
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
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise ToolError(NOT_FOUND, error_msg) from e
        elif "no recipe" in error_msg.lower():
            raise ToolError(NO_RECIPE, error_msg) from e
        else:
            raise ToolError(INVALID_PARAMS, error_msg) from e

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)
    result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}

    return result


async def _handle_set_prices(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Set storefront prices for goods at your business.

    NPC consumers buy at set prices every minute (fast tick).
    Lower prices attract more NPC customers.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.government.jail import check_jail

    try:
        check_jail(agent, clock)
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
    except TypeError, ValueError:
        raise ToolError(INVALID_PARAMS, "Parameter 'price' must be a number")

    if price <= 0:
        raise ToolError(INVALID_PARAMS, "Parameter 'price' must be greater than 0")
    if price > 1_000_000:
        raise ToolError(INVALID_PARAMS, "Parameter 'price' must be at most 1,000,000")

    try:
        business_id = _uuid.UUID(business_id_str)
    except ValueError, AttributeError:
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
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise ToolError(NOT_FOUND, error_msg) from e
        else:
            raise ToolError(INVALID_PARAMS, error_msg) from e

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)
    result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}

    return result
