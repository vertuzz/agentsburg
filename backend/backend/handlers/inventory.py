"""Business inventory transfer handler."""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.errors import (
    COOLDOWN_ACTIVE,
    INSUFFICIENT_INVENTORY,
    IN_JAIL,
    INVALID_PARAMS,
    NOT_FOUND,
    STORAGE_FULL,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_business_inventory(
    params: dict,
    agent: "Agent | None",
    db: AsyncSession,
    clock: "Clock",
    redis: "aioredis.Redis",
    settings: "Settings",
) -> dict:
    """
    Transfer goods between personal inventory and a business the agent owns.

    Actions:
      - deposit:  move goods from agent → business
      - withdraw: move goods from business → agent
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    from backend.government.jail import check_jail
    try:
        check_jail(agent, clock)
    except ValueError as e:
        raise ToolError(IN_JAIL, str(e)) from e

    # Parse params
    action = params.get("action")
    valid_actions = ("deposit", "withdraw", "view", "batch_deposit", "batch_withdraw")
    if action not in valid_actions:
        raise ToolError(
            INVALID_PARAMS,
            f"Parameter 'action' is required and must be one of: {', '.join(valid_actions)}.",
        )

    business_id = params.get("business_id")
    if not business_id or not isinstance(business_id, str):
        raise ToolError(INVALID_PARAMS, "Parameter 'business_id' (UUID string) is required.")

    # Look up business and validate ownership
    from backend.models.business import Business

    try:
        biz_uuid = _uuid.UUID(business_id)
    except ValueError:
        raise ToolError(INVALID_PARAMS, f"Invalid business_id: {business_id!r}")

    biz_result = await db.execute(
        select(Business).where(Business.id == biz_uuid)
    )
    business = biz_result.scalar_one_or_none()

    if business is None:
        raise ToolError(NOT_FOUND, f"Business not found: {business_id}")
    if business.owner_id != agent.id:
        raise ToolError(NOT_FOUND, f"Business not found: {business_id}")
    if business.closed_at is not None:
        raise ToolError(INVALID_PARAMS, f"Business {business.name!r} is closed.")

    # --- View action: return business inventory without cooldown ---
    if action == "view":
        from backend.agents.inventory import get_inventory, get_storage_used
        inventory_items = await get_inventory(db, "business", biz_uuid)
        biz_storage_used = await get_storage_used(db, "business", biz_uuid, settings)
        biz_capacity = settings.economy.business_storage_capacity

        from backend.hints import get_pending_events
        pending_events = await get_pending_events(db, agent)

        # Also show storefront prices
        from backend.models.business import StorefrontPrice
        prices_result = await db.execute(
            select(StorefrontPrice).where(StorefrontPrice.business_id == biz_uuid)
        )
        prices = list(prices_result.scalars().all())

        return {
            "business_id": business_id,
            "business_name": business.name,
            "business_type": business.type_slug,
            "default_product": business.default_recipe_slug,
            "inventory": [item.to_dict() for item in inventory_items],
            "storage": {
                "used": biz_storage_used,
                "capacity": biz_capacity,
                "free": biz_capacity - biz_storage_used,
            },
            "storefront_prices": [
                {"good": p.good_slug, "price": float(p.price)}
                for p in prices
            ],
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 60,
                "message": (
                    f"Business {business.name!r} inventory: "
                    f"{biz_storage_used}/{biz_capacity} storage used."
                ),
            },
        }

    # --- Batch Deposit/Withdraw: multiple goods in one call, single cooldown ---
    if action in ("batch_deposit", "batch_withdraw"):
        goods_list = params.get("goods")
        if not isinstance(goods_list, list) or not goods_list:
            raise ToolError(
                INVALID_PARAMS,
                "Parameter 'goods' must be a non-empty list of {good, quantity} objects.",
            )
        if len(goods_list) > 20:
            raise ToolError(INVALID_PARAMS, "Maximum 20 goods per batch transfer.")

        # Validate all items upfront
        goods_config = {g["slug"]: g for g in settings.goods}
        validated: list[tuple[str, int]] = []
        for i, item in enumerate(goods_list):
            if not isinstance(item, dict):
                raise ToolError(INVALID_PARAMS, f"Item {i}: must be an object with 'good' and 'quantity'.")
            g = item.get("good")
            q = item.get("quantity")
            if not g or not isinstance(g, str) or g not in goods_config:
                raise ToolError(INVALID_PARAMS, f"Item {i}: unknown or missing good slug {g!r}.")
            if not isinstance(q, int) or isinstance(q, bool) or q <= 0:
                raise ToolError(INVALID_PARAMS, f"Item {i}: quantity must be a positive integer.")
            validated.append((g, q))

        # Acquire processing lock + check cooldown
        lock_key = f"lock:transfer:{agent.id}"
        acquired = await redis.set(lock_key, "1", nx=True, ex=300)
        if not acquired:
            raise ToolError(COOLDOWN_ACTIVE, "Transfer already in progress. Try again shortly.")

        try:
            from datetime import datetime, timedelta, timezone
            cooldown_key = f"cooldown:transfer:{agent.id}"
            stored_expiry = await redis.get(cooldown_key)
            now = clock.now()

            if stored_expiry:
                try:
                    expiry_dt = datetime.fromisoformat(stored_expiry)
                    if expiry_dt.tzinfo is None:
                        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                    if now < expiry_dt:
                        remaining = int((expiry_dt - now).total_seconds())
                        raise ToolError(
                            COOLDOWN_ACTIVE,
                            f"Transfer cooldown active. Try again in {remaining} seconds.",
                        )
                except ToolError:
                    raise
                except (ValueError, TypeError):
                    pass

            from backend.agents.inventory import (
                add_to_inventory,
                get_storage_used,
                remove_from_inventory,
            )

            is_deposit = action == "batch_deposit"
            transferred: list[dict] = []
            rollback_stack: list[tuple] = []

            try:
                for g, q in validated:
                    if is_deposit:
                        await remove_from_inventory(db, "agent", agent.id, g, q)
                        rollback_stack.append(("agent", agent.id, "business", biz_uuid, g, q))
                        await add_to_inventory(db, "business", biz_uuid, g, q, settings)
                    else:
                        await remove_from_inventory(db, "business", biz_uuid, g, q)
                        rollback_stack.append(("business", biz_uuid, "agent", agent.id, g, q))
                        await add_to_inventory(db, "agent", agent.id, g, q, settings)
                    transferred.append({"good": g, "quantity": q})
            except (ValueError, ToolError) as exc:
                # Rollback all successful transfers in reverse order
                for src_type, src_id, dst_type, dst_id, g, q in reversed(rollback_stack):
                    try:
                        await remove_from_inventory(db, dst_type, dst_id, g, q)
                        await add_to_inventory(db, src_type, src_id, g, q, settings)
                    except Exception:
                        pass  # Best-effort rollback
                error_msg = str(exc.message if isinstance(exc, ToolError) else exc)
                raise ToolError(
                    STORAGE_FULL if "storage" in error_msg.lower() else INSUFFICIENT_INVENTORY,
                    f"Batch transfer failed and was rolled back. Error: {error_msg}",
                ) from exc

            # Set cooldown (single cooldown for entire batch)
            cooldown_seconds = 10
            expiry_time = now + timedelta(seconds=cooldown_seconds)
            await redis.set(cooldown_key, expiry_time.isoformat(), ex=max(cooldown_seconds * 2, 120))

            agent_storage_used = await get_storage_used(db, "agent", agent.id, settings)
            agent_capacity = settings.economy.agent_storage_capacity
            biz_storage_used = await get_storage_used(db, "business", biz_uuid, settings)
            biz_capacity = settings.economy.business_storage_capacity

        finally:
            await redis.delete(lock_key)

        from backend.hints import get_pending_events
        pending_events = await get_pending_events(db, agent)

        return {
            "transferred": transferred,
            "count": len(transferred),
            "action": action,
            "business_id": business_id,
            "business_name": business.name,
            "agent_storage": {
                "used": agent_storage_used,
                "capacity": agent_capacity,
                "free": agent_capacity - agent_storage_used,
            },
            "business_storage": {
                "used": biz_storage_used,
                "capacity": biz_capacity,
                "free": biz_capacity - biz_storage_used,
            },
            "cooldown_seconds": cooldown_seconds,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": cooldown_seconds,
                "message": (
                    f"Batch transferred {len(transferred)} goods "
                    f"{'into' if is_deposit else 'from'} "
                    f"{business.name!r}. Next transfer in {cooldown_seconds}s."
                ),
            },
        }

    # --- Deposit/Withdraw: require good and quantity params ---
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

    # Processing lock
    lock_key = f"lock:transfer:{agent.id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=300)
    if not acquired:
        raise ToolError(COOLDOWN_ACTIVE, "Transfer already in progress. Try again shortly.")

    try:
        # Check cooldown
        from datetime import datetime, timedelta, timezone
        cooldown_key = f"cooldown:transfer:{agent.id}"
        stored_expiry = await redis.get(cooldown_key)
        now = clock.now()

        if stored_expiry:
            try:
                expiry_dt = datetime.fromisoformat(stored_expiry)
                if expiry_dt.tzinfo is None:
                    expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                if now < expiry_dt:
                    remaining = int((expiry_dt - now).total_seconds())
                    raise ToolError(
                        COOLDOWN_ACTIVE,
                        f"Transfer cooldown active. Try again in {remaining} seconds.",
                    )
            except ToolError:
                raise
            except (ValueError, TypeError):
                pass  # Corrupted key — ignore

        from backend.agents.inventory import (
            add_to_inventory,
            get_storage_used,
            remove_from_inventory,
        )

        if action == "deposit":
            # Agent → Business
            try:
                await remove_from_inventory(db, "agent", agent.id, good_slug, quantity)
            except ValueError as e:
                raise ToolError(INSUFFICIENT_INVENTORY, str(e)) from e
            try:
                item = await add_to_inventory(db, "business", biz_uuid, good_slug, quantity, settings)
            except ValueError as e:
                # Rollback: return goods to agent
                await add_to_inventory(db, "agent", agent.id, good_slug, quantity, settings)
                raise ToolError(STORAGE_FULL, str(e)) from e
        else:
            # Business → Agent
            try:
                await remove_from_inventory(db, "business", biz_uuid, good_slug, quantity)
            except ValueError as e:
                raise ToolError(INSUFFICIENT_INVENTORY, str(e)) from e
            try:
                item = await add_to_inventory(db, "agent", agent.id, good_slug, quantity, settings)
            except ValueError as e:
                # Rollback: return goods to business
                await add_to_inventory(db, "business", biz_uuid, good_slug, quantity, settings)
                raise ToolError(STORAGE_FULL, str(e)) from e

        # Set cooldown (reduced from 30s to 10s per player feedback)
        cooldown_seconds = 10
        expiry_time = now + timedelta(seconds=cooldown_seconds)
        await redis.set(cooldown_key, expiry_time.isoformat(), ex=max(cooldown_seconds * 2, 120))

        # Compute storage states
        agent_storage_used = await get_storage_used(db, "agent", agent.id, settings)
        agent_capacity = settings.economy.agent_storage_capacity
        biz_storage_used = await get_storage_used(db, "business", biz_uuid, settings)
        biz_capacity = settings.economy.business_storage_capacity

    finally:
        await redis.delete(lock_key)

    from backend.hints import get_pending_events
    pending_events = await get_pending_events(db, agent)

    return {
        "transferred": quantity,
        "good": good_slug,
        "action": action,
        "business_id": business_id,
        "business_name": business.name,
        "agent_storage": {
            "used": agent_storage_used,
            "capacity": agent_capacity,
            "free": agent_capacity - agent_storage_used,
        },
        "business_storage": {
            "used": biz_storage_used,
            "capacity": biz_capacity,
            "free": biz_capacity - biz_storage_used,
        },
        "cooldown_seconds": cooldown_seconds,
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": cooldown_seconds,
            "message": (
                f"Transferred {quantity}x {good_slug} "
                f"{'into' if action == 'deposit' else 'from'} "
                f"{business.name!r}. Next transfer in {cooldown_seconds}s."
            ),
        },
    }
