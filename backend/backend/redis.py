"""
Redis connection management for Agent Economy.

Redis is used for:
- Cooldown tracking (gather, work) with TTL-based keys
- Tick locking (prevent overlapping tick runs)
- Tick boundary tracking (last hourly/daily/weekly run timestamps)
- General caching

The connection is created during app lifespan and stored on app.state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from fastapi import Request


async def create_redis(url: str) -> aioredis.Redis:
    """Create and verify an async Redis connection."""
    client = aioredis.from_url(
        url,
        encoding="utf-8",
        decode_responses=True,
        health_check_interval=30,
    )
    # Verify connectivity
    await client.ping()
    return client


async def close_redis(client: aioredis.Redis) -> None:
    """Gracefully close the Redis connection."""
    await client.aclose()


def get_redis(request: Request) -> aioredis.Redis:
    """
    FastAPI dependency that returns the shared Redis client from app.state.

    Does NOT yield — Redis connections are multiplexed and the single client
    instance is safe to share across requests.
    """
    return request.app.state.redis
