"""
Global spectator event feed for Agent Economy.

Redis-backed event list visible to unauthenticated spectators.
Events are emitted during ticks alongside per-agent events and
narrated into human-readable text via narrative.py.

Redis key: spectator:feed  (list, capped at MAX_EVENTS, 48h TTL)
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from backend.spectator.narrative import narrate

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock

logger = logging.getLogger(__name__)

FEED_KEY = "spectator:feed"
MAX_EVENTS = 200
FEED_TTL = 172800  # 48 hours

DRAMA_LEVELS = ("routine", "notable", "critical")


async def emit_spectator_event(
    redis: aioredis.Redis,
    event_type: str,
    detail: dict,
    clock: Clock,
    drama: str = "routine",
) -> None:
    """Push an event to the global spectator feed with narrative text."""
    if drama not in DRAMA_LEVELS:
        drama = "routine"

    narrative = narrate(event_type, detail)
    event = {
        "type": event_type,
        "detail": detail,
        "text": narrative["text"],
        "drama": drama,
        "category": narrative["category"],
        "ts": clock.now().isoformat(),
    }
    try:
        await redis.lpush(FEED_KEY, json.dumps(event))
        await redis.ltrim(FEED_KEY, 0, MAX_EVENTS - 1)
        await redis.expire(FEED_KEY, FEED_TTL)
    except Exception:
        logger.warning("Failed to emit spectator event %s", event_type)


async def get_spectator_feed(
    redis: aioredis.Redis,
    limit: int = 50,
    min_drama: str = "routine",
    category: str | None = None,
) -> list[dict]:
    """Read events from the global feed with optional filters."""
    raw = await redis.lrange(FEED_KEY, 0, MAX_EVENTS - 1)

    min_idx = DRAMA_LEVELS.index(min_drama) if min_drama in DRAMA_LEVELS else 0
    events = []
    for r in raw:
        try:
            ev = json.loads(r)
        except json.JSONDecodeError, TypeError:
            continue

        # Filter by drama level
        ev_drama = ev.get("drama", "routine")
        ev_idx = DRAMA_LEVELS.index(ev_drama) if ev_drama in DRAMA_LEVELS else 0
        if ev_idx < min_idx:
            continue

        # Filter by category
        if category and ev.get("category") != category:
            continue

        events.append(ev)
        if len(events) >= limit:
            break

    return events


async def get_activity_pulse(
    redis: aioredis.Redis,
    clock: Clock,
) -> dict:
    """Count events in the last 1h and 24h."""
    raw = await redis.lrange(FEED_KEY, 0, MAX_EVENTS - 1)
    now = clock.now()
    one_hour_ago = now - timedelta(hours=1)
    one_day_ago = now - timedelta(hours=24)

    count_1h = 0
    count_24h = 0

    for r in raw:
        try:
            ev = json.loads(r)
            from datetime import UTC, datetime

            ts = datetime.fromisoformat(ev["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if ts >= one_day_ago:
                count_24h += 1
                if ts >= one_hour_ago:
                    count_1h += 1
        except json.JSONDecodeError, TypeError, KeyError, ValueError:
            continue

    return {"count_1h": count_1h, "count_24h": count_24h}
