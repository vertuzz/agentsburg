"""
Gathering domain logic for Agent Economy.

Free resource extraction — the economic floor. Any agent can gather
tier-1 resources with no cost, only a per-agent per-resource cooldown.

Cooldowns are tracked in Redis using the Clock time (not real time):
    cooldown:gather:{agent_id}:{resource_slug} → ISO timestamp of when cooldown expires

This allows MockClock to control cooldown behavior in tests.
The key has a real-time TTL of 2x the cooldown (safety buffer) so stale
keys don't accumulate forever.

No homeless penalty on gathering — gathering is the economic floor activity.
Homeless penalty applies to production/work only (via housing.py).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from backend.agents.inventory import add_to_inventory, get_storage_used
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    import uuid

    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent

logger = logging.getLogger(__name__)


def _gather_cooldown_key(agent_id: uuid.UUID, resource_slug: str) -> str:
    """Redis key for gather cooldown expiry timestamp."""
    return f"cooldown:gather:{agent_id}:{resource_slug}"


async def gather(
    db: AsyncSession,
    redis: aioredis.Redis,
    agent: Agent,
    resource_slug: str,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Gather one unit of a gatherable resource.

    Flow:
    1. Validate the resource exists and is gatherable (tier 1)
    2. Check cooldown by comparing clock.now() against stored expiry timestamp
    3. Check storage capacity — return error if full
    4. Add 1 unit to agent inventory
    5. Store new cooldown expiry in Redis (with real-time TTL safety buffer)
    6. Return success with item details and cooldown info

    Cooldowns use clock.now() for MockClock compatibility in tests.

    Args:
        db:            Active async database session.
        redis:         Redis client for cooldown tracking.
        agent:         The gathering agent.
        resource_slug: Slug of the resource to gather.
        clock:         Clock for cooldown timestamp comparison.
        settings:      Application settings.

    Returns:
        Dict with gathered item info and cooldown details.

    Raises:
        ValueError: If resource is invalid, on cooldown, or storage is full.
    """
    # Look up the good from config
    goods_config = {g["slug"]: g for g in settings.goods}
    good_data = goods_config.get(resource_slug)

    if good_data is None:
        raise ValueError(
            f"Unknown resource: {resource_slug!r}. "
            f"Available resources: {[s for s, g in goods_config.items() if g.get('gatherable')]}"
        )

    if not good_data.get("gatherable", False):
        raise ValueError(
            f"{good_data.get('name', resource_slug)!r} is not gatherable. "
            f"Only tier-1 raw resources can be gathered for free."
        )

    now = clock.now()

    # Acquire a processing lock atomically to prevent concurrent gather races
    lock_key = f"lock:gather:{agent.id}:{resource_slug}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=300)  # 5 min safety TTL
    if not acquired:
        raise ValueError("Gather already in progress for this resource. Try again shortly.")

    try:
        # Check cooldown using stored expiry timestamp
        cooldown_key = _gather_cooldown_key(agent.id, resource_slug)
        stored_expiry = await redis.get(cooldown_key)

        if stored_expiry:
            try:
                expiry_dt = datetime.fromisoformat(stored_expiry)
                # Make timezone-aware if needed
                if expiry_dt.tzinfo is None:
                    expiry_dt = expiry_dt.replace(tzinfo=UTC)
                if now < expiry_dt:
                    remaining = int((expiry_dt - now).total_seconds())
                    raise ValueError(
                        f"Gather cooldown active for {good_data.get('name', resource_slug)}. "
                        f"Try again in {remaining} seconds."
                    )
            except (ValueError, TypeError) as e:
                if "cooldown active" in str(e).lower():
                    raise
                # Corrupted key — ignore and allow gathering
                logger.warning("Corrupted cooldown key %s: %r", cooldown_key, stored_expiry)

        # Calculate cooldown duration
        base_cooldown = good_data.get(
            "gather_cooldown_seconds",
            settings.economy.base_gather_cooldown,
        )

        # No homeless penalty on gathering — it's the economic floor activity.
        # Homeless penalty still applies to production/work (2x cooldown there).
        cooldown_seconds = base_cooldown
        homeless_penalty_applied = False

        # Try to add to inventory — this checks storage capacity
        try:
            item = await add_to_inventory(
                db=db,
                owner_type="agent",
                owner_id=agent.id,
                good_slug=resource_slug,
                quantity=1,
                settings=settings,
            )
        except ValueError as e:
            raise ValueError(str(e)) from e

        # Credit agent with cash — the "roadside sale" value.
        # This is the economic floor: even without a marketplace or business,
        # gathering produces income. cash_on_gather is set lower than
        # base_value so business production is more profitable than raw gathering.
        gather_cash = Decimal(str(good_data.get("cash_on_gather", good_data.get("base_value", 1))))
        agent.balance = Decimal(str(agent.balance)) + gather_cash

        txn = Transaction(
            type="gather",
            from_agent_id=None,  # from the environment
            to_agent_id=agent.id,
            amount=float(gather_cash),
            metadata_json={
                "resource": resource_slug,
                "base_value": float(good_data.get("base_value", 1)),
                "tick_time": now.isoformat(),
            },
        )
        db.add(txn)

        # Store cooldown expiry timestamp using clock time
        from datetime import timedelta

        expiry_time = now + timedelta(seconds=cooldown_seconds)
        expiry_str = expiry_time.isoformat()

        # Use real-time TTL = 2x cooldown as safety buffer to prevent key accumulation
        # If clock is MockClock, keys may persist in Redis longer, but that's OK
        real_ttl = max(cooldown_seconds * 2, 120)
        await redis.set(cooldown_key, expiry_str, ex=real_ttl)
    finally:
        await redis.delete(lock_key)

    # Compute storage state after gathering
    storage_used = await get_storage_used(db, "agent", agent.id, settings)
    storage_capacity = settings.economy.agent_storage_capacity
    storage_free = storage_capacity - storage_used

    logger.debug(
        "Agent %s gathered 1x %s (cooldown: %ds, expires: %s, homeless: %s)",
        agent.name,
        resource_slug,
        cooldown_seconds,
        expiry_str,
        homeless_penalty_applied,
    )

    hints_message = (
        f"You gathered 1x {good_data.get('name', resource_slug)} "
        f"and earned {float(gather_cash):.2f} cash. "
        f"Next gather available in {cooldown_seconds}s."
    )
    if storage_free <= storage_capacity * 0.2:
        hints_message += f" WARNING: Storage nearly full ({storage_used}/{storage_capacity})."

    return {
        "gathered": resource_slug,
        "name": good_data.get("name", resource_slug),
        "quantity": 1,
        "new_inventory_quantity": item.quantity,
        "cooldown_seconds": cooldown_seconds,
        "homeless_penalty_applied": homeless_penalty_applied,
        "base_value": float(good_data.get("base_value", 1)),
        "cash_earned": float(gather_cash),
        "storage": {
            "used": storage_used,
            "capacity": storage_capacity,
            "free": storage_free,
        },
        "_hints": {
            "check_back_seconds": cooldown_seconds,
            "message": hints_message,
        },
    }
