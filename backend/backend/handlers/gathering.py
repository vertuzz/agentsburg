"""Resource gathering and inventory discard handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from backend.errors import (
    COOLDOWN_ACTIVE,
    IN_JAIL,
    INSUFFICIENT_INVENTORY,
    INVALID_PARAMS,
    STORAGE_FULL,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


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

    from backend.government.jail import check_jail
    try:
        check_jail(agent, clock)
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
        error_msg = str(e)
        # Detect specific error types for better error codes
        if "cooldown active" in error_msg.lower():
            raise ToolError(COOLDOWN_ACTIVE, error_msg) from e
        elif "storage full" in error_msg.lower():
            raise ToolError(STORAGE_FULL, error_msg) from e
        elif "not a gatherable" in error_msg.lower() or "unknown" in error_msg.lower():
            raise ToolError(INVALID_PARAMS, error_msg) from e
        else:
            raise ToolError(INVALID_PARAMS, error_msg) from e

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


async def _handle_inventory_discard(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Destroy goods from the agent's personal inventory.

    Use this to free storage space when stuck (e.g., storage full, can't cancel orders).
    Discarded goods are permanently lost.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.government.jail import check_jail
    try:
        check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    good_slug = params.get("good")
    if not good_slug or not isinstance(good_slug, str):
        raise ToolError(INVALID_PARAMS, "Parameter 'good' (good slug) is required.")

    quantity = params.get("quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ToolError(INVALID_PARAMS, "Parameter 'quantity' must be a positive integer.")

    # Validate good exists
    goods_config = {g["slug"]: g for g in settings.goods}
    if good_slug not in goods_config:
        raise ToolError(INVALID_PARAMS, f"Unknown good: {good_slug!r}.")

    from backend.agents.inventory import get_storage_used, remove_from_inventory

    try:
        await remove_from_inventory(db, "agent", agent.id, good_slug, quantity)
    except ValueError as e:
        raise ToolError(INSUFFICIENT_INVENTORY, str(e)) from e

    storage_used = await get_storage_used(db, "agent", agent.id, settings)
    capacity = settings.economy.agent_storage_capacity

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)

    return {
        "discarded": {
            "good": good_slug,
            "quantity": quantity,
        },
        "storage": {
            "used": storage_used,
            "capacity": capacity,
            "free": capacity - storage_used,
        },
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 60,
            "message": f"Discarded {quantity}x {good_slug}. Storage: {storage_used}/{capacity}.",
        },
    }
