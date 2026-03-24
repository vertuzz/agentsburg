"""
Economy event notifications for Agent Economy.

Lightweight Redis-backed event store. Events are emitted during ticks
(rent, food, order fills, loan payments) and retrieved via GET /v1/events.

Events are stored per-agent in Redis lists with a 24h TTL and a cap of 50.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock

logger = logging.getLogger(__name__)

MAX_EVENTS = 50
EVENT_TTL = 86400  # 24 hours


def _events_key(agent_id: UUID) -> str:
    return f"events:{agent_id}"


async def emit_event(
    redis: aioredis.Redis,
    agent_id: UUID,
    event_type: str,
    detail: dict,
    clock: Clock,
) -> None:
    """Push an event to the agent's Redis event list."""
    key = _events_key(agent_id)
    event = {
        "type": event_type,
        "detail": detail,
        "ts": clock.now().isoformat(),
    }
    try:
        await redis.lpush(key, json.dumps(event))
        await redis.ltrim(key, 0, MAX_EVENTS - 1)
        await redis.expire(key, EVENT_TTL)
    except Exception:
        logger.warning("Failed to emit event %s for agent %s", event_type, agent_id)


async def get_events(
    redis: aioredis.Redis,
    agent_id: UUID,
    limit: int = 20,
) -> list[dict]:
    """Read recent events for an agent."""
    key = _events_key(agent_id)
    raw = await redis.lrange(key, 0, limit - 1)
    events = []
    for r in raw:
        try:
            events.append(json.loads(r))
        except json.JSONDecodeError, TypeError:
            continue
    return events


async def count_events(
    redis: aioredis.Redis,
    agent_id: UUID,
) -> int:
    """Count events in the agent's event list."""
    key = _events_key(agent_id)
    return await redis.llen(key) or 0
