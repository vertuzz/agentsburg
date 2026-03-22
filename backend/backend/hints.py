"""
Response hints helpers for Agent Economy tools.

Every tool response includes a `_hints` dict that tells the agent:
- How many events are waiting for them (unread messages + pending trades)
- When to check back (next cooldown, next loan payment, next tick)
- Any cooldown remaining for time-limited actions

This is the agent's primary mechanism for knowing when to poll —
the economy is real-time and agents should actively monitor their state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from backend.models.agent import Agent


async def get_pending_events(db: AsyncSession, agent: "Agent") -> int:
    """
    Calculate the total number of pending events for an agent.

    Pending events include:
    - Unread messages in the agent's inbox
    - Pending (incoming) direct trade proposals

    This count is included in every tool's _hints to tell agents
    when they have things waiting for their attention.

    Args:
        db:    Active async database session.
        agent: The authenticated agent.

    Returns:
        Total count of pending events.
    """
    from sqlalchemy import func as sqlfunc

    count = 0

    # Unread messages
    try:
        from backend.models.message import Message
        msg_result = await db.execute(
            select(sqlfunc.count(Message.id)).where(
                Message.to_agent_id == agent.id,
                Message.read == False,  # noqa: E712
            )
        )
        count += msg_result.scalar_one() or 0
    except Exception:
        pass

    # Pending incoming trade proposals (target = this agent, status = pending)
    try:
        from backend.models.marketplace import Trade
        trade_result = await db.execute(
            select(sqlfunc.count(Trade.id)).where(
                Trade.target_id == agent.id,
                Trade.status == "pending",
            )
        )
        count += trade_result.scalar_one() or 0
    except Exception:
        pass

    return count


def make_hints(
    pending_events: int = 0,
    check_back_seconds: int = 60,
    cooldown_remaining: int | None = None,
    **extra,
) -> dict:
    """
    Build a standard _hints dict for tool responses.

    Args:
        pending_events:    Count of unread messages + pending trades.
        check_back_seconds: Suggested polling interval in seconds.
        cooldown_remaining: Seconds until this specific action's cooldown ends.
        **extra:           Any additional hint key/values to include.

    Returns:
        A dict suitable for inclusion as `_hints` in a tool response.
    """
    hints: dict = {
        "pending_events": pending_events,
        "check_back_seconds": check_back_seconds,
    }
    if cooldown_remaining is not None:
        hints["cooldown_remaining"] = cooldown_remaining
    hints.update(extra)
    return hints
