"""
Agent domain service for Agent Economy.

Handles agent creation (signup) and status retrieval. All persistence goes
through SQLAlchemy async sessions — callers provide the session.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent

if TYPE_CHECKING:
    from backend.clock import Clock


async def signup(db: AsyncSession, name: str, model: str | None = None, settings=None) -> dict:
    """
    Register a new agent with the given name.

    Generates two opaque tokens:
    - action_token: used for MCP tool calls (keep secret)
    - view_token:   used for dashboard access (read-only, safe to share)

    Args:
        db:    Active async database session.
        name:  Desired agent name. Must be unique in the system.
        model: Optional AI model name (e.g., "Claude Opus 4.6", "GPT 5.4").

    Returns:
        Dict with keys: name, action_token, view_token, model.

    Raises:
        ValueError: if the name is already taken.
    """
    # Check for name collision before inserting
    existing = await db.execute(select(Agent).where(Agent.name == name))
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"Agent name {name!r} is already taken")

    action_token = secrets.token_urlsafe(32)
    view_token = secrets.token_urlsafe(32)

    starting_balance = 0
    if settings and hasattr(settings, 'economy'):
        starting_balance = getattr(settings.economy, 'agent_starting_balance', 0)

    agent = Agent(
        name=name,
        action_token=action_token,
        view_token=view_token,
        balance=starting_balance,
        model=model,
    )
    db.add(agent)
    await db.flush()  # populate id + timestamps without committing

    return {
        "name": agent.name,
        "action_token": action_token,
        "view_token": view_token,
        "model": agent.model,
    }


async def get_agent_by_action_token(db: AsyncSession, token: str) -> Agent | None:
    """
    Look up an agent by their action token.

    Returns None if no agent with this token exists.
    """
    result = await db.execute(select(Agent).where(Agent.action_token == token))
    return result.scalar_one_or_none()


async def get_agent_by_view_token(db: AsyncSession, token: str) -> Agent | None:
    """
    Look up an agent by their view (dashboard) token.

    Returns None if no agent with this token exists.
    """
    result = await db.execute(select(Agent).where(Agent.view_token == token))
    return result.scalar_one_or_none()


async def get_status(db: AsyncSession, agent: Agent, clock: "Clock") -> dict:
    """
    Return the full status dict for an agent.

    This is the payload returned by the get_status MCP tool. It gives the
    agent a complete picture of their current situation including:
    - Balance and housing
    - Employment (None for now, populated in Phase 3)
    - Businesses (empty list for now, populated in Phase 3)
    - Criminal record (violation count, jail status)
    - Pending hints for the agent's next actions

    Args:
        db:    Active async database session.
        agent: The authenticated agent whose status to return.
        clock: Clock instance for computing relative times.

    Returns:
        A JSON-serializable dict suitable for MCP tool output.
    """
    now = clock.now()

    # Jail status
    jailed = agent.is_jailed(now)
    jail_remaining_seconds: float | None = None
    if jailed and agent.jail_until is not None:
        jail_remaining_seconds = (agent.jail_until - now).total_seconds()

    # Housing
    homeless = agent.is_homeless()

    # Build status payload
    status = {
        "name": agent.name,
        "model": agent.model,
        "balance": float(agent.balance),
        "housing": {
            "zone_id": str(agent.housing_zone_id) if agent.housing_zone_id else None,
            "homeless": homeless,
            "penalties": (
                ["cannot_register_business", "reduced_work_efficiency", "higher_crime_detection"]
                if homeless
                else []
            ),
        },
        # Phase 3: will be populated with actual employment data
        "employment": None,
        # Phase 3: will be populated with owned business list
        "businesses": [],
        "criminal_record": {
            "violation_count": agent.violation_count,
            "jailed": jailed,
            "jail_until": agent.jail_until.isoformat() if agent.jail_until else None,
            "jail_remaining_seconds": jail_remaining_seconds,
        },
        "bankruptcy_count": agent.bankruptcy_count,
        # Phase 2+: cooldowns populated from Redis
        "cooldowns": {},
        # Phase 2+: pending events from background processing
        "pending_events": 0,
        "_hints": {
            "pending_events": 0,
            "check_back_seconds": 60,
        },
    }

    return status
