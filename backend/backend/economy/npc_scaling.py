"""
NPC activity scaling for Agent Economy.

Computes a scaling factor (0.1–1.0) based on how many real players are
currently online. When no players are active, NPCs run at full capacity
to keep the city alive. As players join, NPCs step back.

"Online" = made an authenticated API call in the last 30 minutes.
Tracked via Redis keys with 30-min TTL set in rest/common.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.config import Settings

logger = logging.getLogger(__name__)

ACTIVITY_KEY_PREFIX = "agent:active:"


async def get_online_player_count(redis: aioredis.Redis) -> int:
    """Count non-NPC agents with API activity in the last 30 minutes.

    NPC operations never go through REST, so only real players have
    ``agent:active:*`` keys.
    """
    count = 0
    cursor: int | bytes = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{ACTIVITY_KEY_PREFIX}*", count=200)
        count += len(keys)
        if cursor == 0:
            break
    return count


def compute_npc_activity_factor(online_players: int, settings: Settings) -> float:
    """Return a scaling factor for NPC activity (0.1–1.0).

    - 0 players → 1.0 (full NPC activity, city stays alive)
    - ``target_player_count`` or more → ``min_activity_factor`` (NPCs step back)
    - Linear interpolation in between
    """
    target = getattr(settings.economy, "npc_target_player_count", 20)
    min_factor = getattr(settings.economy, "npc_min_activity_factor", 0.1)

    if target <= 0:
        return 1.0

    factor = 1.0 - (online_players / target)
    return max(min_factor, min(1.0, factor))
