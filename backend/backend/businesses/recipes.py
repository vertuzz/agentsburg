"""
Recipe lookup, validation, and cooldown calculation for production.

Used by the work() function to determine what to produce and how long
the cooldown should be.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    import uuid

    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent

logger = logging.getLogger(__name__)


def _work_cooldown_key(agent_id: uuid.UUID) -> str:
    """Redis key for the per-agent global work cooldown expiry timestamp."""
    return f"cooldown:work:{agent_id}"


async def _get_government_modifier(db: AsyncSession, settings: Settings) -> float:
    """
    Get the current government production_cooldown_modifier.

    Queries GovernmentState for the current template's production_cooldown_modifier.
    Falls back to 1.0 if government tables don't exist or state is missing.
    """
    try:
        from backend.government.service import get_policy_params
        from backend.models.government import GovernmentState

        result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
        govt = result.scalar_one_or_none()
        if not govt:
            return 1.0
        params = get_policy_params(settings, govt.current_template_slug)
        return float(params.get("production_cooldown_modifier", 1.0))
    except Exception:
        return 1.0


async def get_work_cooldown_remaining(
    redis: aioredis.Redis,
    agent: Agent,
    clock: Clock,
) -> int | None:
    """
    Check how many seconds remain on an agent's work cooldown.

    Returns None if not on cooldown, or the remaining seconds if active.

    Used by get_status to show cooldown info.
    """
    cooldown_key = _work_cooldown_key(agent.id)
    stored_expiry = await redis.get(cooldown_key)

    if not stored_expiry:
        return None

    try:
        expiry_dt = datetime.fromisoformat(stored_expiry)
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=UTC)
        now = clock.now()
        if now < expiry_dt:
            return int((expiry_dt - now).total_seconds())
    except ValueError, TypeError:
        pass

    return None
