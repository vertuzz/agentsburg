"""Economy event notification handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from backend.errors import UNAUTHORIZED, ToolError

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_events(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Retrieve recent economy events for the authenticated agent.

    Events include: rent_charged, food_charged, evicted, order_filled,
    loan_payment. Events expire after 24 hours.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    limit = params.get("limit", 20)
    if not isinstance(limit, int) or limit < 1:
        limit = 20
    limit = min(limit, 50)

    from backend.events import get_events

    events = await get_events(redis, agent.id, limit=limit)

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)

    return {
        "events": events,
        "count": len(events),
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 60,
            "message": f"Showing {len(events)} recent economy events.",
        },
    }
