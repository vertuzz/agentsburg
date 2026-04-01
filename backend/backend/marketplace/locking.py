"""
Locking helpers for marketplace mutations.

Marketplace order placement, cancellation, and matching can touch multiple
agent balances plus inventory and order rows.  To avoid deadlocks we:
  - serialize same-good order book mutations with a transaction-scoped
    advisory lock
  - acquire agent row locks in a stable UUID order before touching dependent
    inventory or balance state
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.models.agent import Agent

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession


def market_good_lock_key(good_slug: str) -> int:
    """Return a stable signed 64-bit advisory lock key for a market good."""
    digest = hashlib.blake2b(
        good_slug.encode("utf-8"),
        digest_size=8,
        person=b"agentsburg-mkt",
    ).digest()
    unsigned_key = int.from_bytes(digest, "big", signed=False)
    if unsigned_key >= 1 << 63:
        return unsigned_key - (1 << 64)
    return unsigned_key


async def lock_market_good(
    db: AsyncSession,
    good_slug: str,
) -> None:
    """Serialize marketplace mutations for a single good within the current transaction."""
    await db.execute(select(func.pg_advisory_xact_lock(market_good_lock_key(good_slug))))


async def lock_agents_in_order(
    db: AsyncSession,
    agent_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, Agent]:
    """Lock agent rows in stable UUID order and return refreshed ORM rows by id."""
    locked_agents: dict[uuid.UUID, Agent] = {}

    for agent_id in sorted(set(agent_ids), key=str):
        agent_row = await db.execute(
            select(Agent).where(Agent.id == agent_id).with_for_update().execution_options(populate_existing=True)
        )
        agent = agent_row.scalar_one_or_none()
        if agent is None:
            raise ValueError(f"Agent not found during marketplace lock acquisition: {agent_id}")
        locked_agents[agent_id] = agent

    return locked_agents
