"""
Escrow logic for direct agent-to-agent trades in Agent Economy.

Handles:
  - Returning escrowed items/money to proposers (reject, cancel, expire)
  - Expiring trades whose timeout has elapsed (called by fast tick)
  - Bulk-cancelling trades for an agent (called during bankruptcy)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.inventory import add_to_inventory
from backend.models.agent import Agent
from backend.models.marketplace import Trade

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def expire_trades(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Expire all pending trades whose timeout has elapsed.

    Called by the fast tick. Returns escrowed items/money to proposers.

    Args:
        db:      Active async database session.
        clock:   Clock for current time.
        settings: Application settings.

    Returns:
        Dict with count of expired trades.
    """
    now = clock.now()

    # Find all expired pending trades
    result = await db.execute(
        select(Trade).where(
            Trade.status == "pending",
            Trade.expires_at < now,
        )
    )
    expired_trades = list(result.scalars().all())

    if not expired_trades:
        return {"expired": 0}

    count = 0
    for trade in expired_trades:
        # Load proposer
        proposer_result = await db.execute(
            select(Agent).where(Agent.id == trade.proposer_id)
        )
        proposer = proposer_result.scalar_one_or_none()

        if proposer is not None:
            await return_escrow_to_proposer(db, trade, proposer, settings)

        trade.status = "expired"
        trade.escrow_locked = False
        count += 1

    if count:
        await db.flush()
        logger.info("Expired %d trades", count)

    return {"expired": count}


async def cancel_agent_trades(
    db: AsyncSession,
    agent: Agent,
    settings: "Settings",
) -> int:
    """
    Cancel all pending trades involving an agent.

    Used during bankruptcy. Returns escrow to the correct parties.

    Args:
        db:      Active async database session.
        agent:   The agent being bankrupted.
        settings: Application settings.

    Returns:
        Count of cancelled trades.
    """
    # Find all pending trades where agent is proposer or target
    result = await db.execute(
        select(Trade).where(
            Trade.status == "pending",
            (Trade.proposer_id == agent.id) | (Trade.target_id == agent.id),
        )
    )
    pending_trades = list(result.scalars().all())
    count = 0

    for trade in pending_trades:
        if trade.proposer_id == agent.id:
            # Agent is proposer — return their escrow to them
            await return_escrow_to_proposer(db, trade, agent, settings)
        else:
            # Agent is target — return proposer's escrow to them
            proposer_result = await db.execute(
                select(Agent).where(Agent.id == trade.proposer_id)
            )
            proposer = proposer_result.scalar_one_or_none()
            if proposer is not None:
                await return_escrow_to_proposer(db, trade, proposer, settings)

        trade.status = "cancelled"
        trade.escrow_locked = False
        count += 1

    if count:
        await db.flush()
        logger.info(
            "Cancelled %d trades for agent %s (bankruptcy)",
            count,
            agent.name,
        )

    return count


async def return_escrow_to_proposer(
    db: AsyncSession,
    trade: Trade,
    proposer: Agent,
    settings: "Settings",
) -> None:
    """
    Return escrowed items and money to the proposer.

    Called on reject, cancel, and expire.
    """
    if not trade.escrow_locked:
        return  # Already returned

    # Return offered goods
    for item in (trade.offer_items or []):
        try:
            await add_to_inventory(
                db, "agent", proposer.id, item["good_slug"], item["quantity"], settings
            )
        except ValueError:
            # Storage full during bankruptcy/cancel — best effort
            logger.warning(
                "Could not return %dx %s to %s (storage full)",
                item["quantity"],
                item["good_slug"],
                proposer.name,
            )

    # Return offered money
    offer_money = Decimal(str(trade.offer_money))
    if offer_money > 0:
        proposer.balance = Decimal(str(proposer.balance)) + offer_money
        await db.flush()
