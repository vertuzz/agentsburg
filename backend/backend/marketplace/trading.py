"""
Direct agent-to-agent trading with escrow for Agent Economy.

Two-step handshake:
  1. proposer calls propose_trade() — items locked in escrow
  2. target calls respond_trade() with accept=True/False

If no response before expires_at, expire_trades() returns escrow to proposer.

IMPORTANT TAX DESIGN:
  Accepted direct trades create Transaction records with type="trade".
  This is intentionally NOT "marketplace" — the tax authority only audits
  "marketplace" transactions. Direct trades are the legal grey area / crime
  opportunity described in the spec.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.inventory import remove_from_inventory
from backend.models.agent import Agent
from backend.models.inventory import InventoryItem
from backend.models.marketplace import Trade

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

# Re-exports from escrow (backwards compatibility)
from backend.marketplace.escrow import (  # noqa: F401
    cancel_agent_trades,
    expire_trades,
)

# Re-exports from trade_responses (backwards compatibility)
from backend.marketplace.trade_responses import (  # noqa: F401
    cancel_trade,
    respond_trade,
)

logger = logging.getLogger(__name__)


async def propose_trade(
    db: AsyncSession,
    agent: Agent,
    target_agent_name: str,
    offer_items: list[dict],
    request_items: list[dict],
    offer_money: Decimal,
    request_money: Decimal,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Propose a direct trade to another agent.

    The proposer's offered items and money are immediately locked in escrow
    (removed from their inventory/balance). The trade stays pending until
    the target responds or it expires.

    Args:
        db:                Active async database session.
        agent:             The proposer agent.
        target_agent_name: Name of the target agent.
        offer_items:       List of {good_slug, quantity} the proposer offers.
        request_items:     List of {good_slug, quantity} the proposer wants.
        offer_money:       Currency the proposer offers.
        request_money:     Currency the proposer wants from target.
        clock:             Clock for expiry calculation.
        settings:          Application settings.

    Returns:
        Dict with trade details.

    Raises:
        ValueError: If target not found, insufficient items/funds, bad params.
    """
    from datetime import timedelta

    # Validate trade has at least some content
    if not offer_items and not request_items and offer_money <= 0 and request_money <= 0:
        raise ValueError("Trade must include at least one offered or requested item or currency")

    # Find target agent
    target_result = await db.execute(select(Agent).where(Agent.name == target_agent_name))
    target = target_result.scalar_one_or_none()
    if target is None:
        raise ValueError(f"Agent {target_agent_name!r} not found")

    if target.id == agent.id:
        raise ValueError("Cannot trade with yourself")

    # Validate offer_items
    offer_items = offer_items or []
    request_items = request_items or []

    for item in offer_items:
        if "good_slug" not in item or "quantity" not in item:
            raise ValueError("Each offer item must have 'good_slug' and 'quantity'")
        if not isinstance(item["quantity"], int) or item["quantity"] <= 0:
            raise ValueError(f"Invalid quantity for {item.get('good_slug')!r}")

    for item in request_items:
        if "good_slug" not in item or "quantity" not in item:
            raise ValueError("Each request item must have 'good_slug' and 'quantity'")
        if not isinstance(item["quantity"], int) or item["quantity"] <= 0:
            raise ValueError(f"Invalid quantity for {item.get('good_slug')!r}")

    if offer_money < 0:
        raise ValueError("offer_money cannot be negative")
    if request_money < 0:
        raise ValueError("request_money cannot be negative")

    # Verify proposer has all offered goods (locked to prevent concurrent removal)
    for item in offer_items:
        slug = item["good_slug"]
        qty = item["quantity"]
        inv_result = await db.execute(
            select(InventoryItem)
            .where(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.good_slug == slug,
            )
            .with_for_update()
        )
        inv_item = inv_result.scalar_one_or_none()
        have = inv_item.quantity if inv_item else 0
        if have < qty:
            raise ValueError(f"Insufficient inventory: have {have}x {slug!r}, need {qty}")

    # Re-lock agent row before balance modification (prevent double-spend).
    # populate_existing=True forces SQLAlchemy to overwrite the cached identity
    # map entry with fresh data from the DB after the lock is acquired.
    agent_row = await db.execute(
        select(Agent).where(Agent.id == agent.id).with_for_update().execution_options(populate_existing=True)
    )
    agent = agent_row.scalar_one()

    # Verify proposer has enough balance for offer_money
    agent_balance = Decimal(str(agent.balance))
    if offer_money > agent_balance:
        raise ValueError(f"Insufficient balance: need {float(offer_money):.2f} but have {float(agent_balance):.2f}")

    # Lock offered goods in escrow (remove from proposer's inventory)
    for item in offer_items:
        await remove_from_inventory(db, "agent", agent.id, item["good_slug"], item["quantity"])

    # Lock offered money (deduct from proposer's balance)
    if offer_money > 0:
        agent.balance = agent_balance - offer_money
        await db.flush()

    # Calculate expiry
    timeout_seconds = getattr(
        settings.economy, "trade_escrow_timeout_seconds", getattr(settings.economy, "trade_escrow_timeout", 3600)
    )
    expires_at = clock.now() + timedelta(seconds=timeout_seconds)

    # Create trade record
    trade = Trade(
        proposer_id=agent.id,
        target_id=target.id,
        offer_items=offer_items,
        request_items=request_items,
        offer_money=float(offer_money),
        request_money=float(request_money),
        status="pending",
        escrow_locked=True,
        expires_at=expires_at,
    )
    db.add(trade)
    await db.flush()

    logger.info(
        "Trade proposed: %s → %s (id: %s, expires: %s)",
        agent.name,
        target.name,
        trade.id,
        expires_at.isoformat(),
    )

    return {
        "trade": trade.to_dict(),
        "proposer": agent.name,
        "target": target.name,
        "message": (
            f"Trade proposal sent to {target.name!r}. "
            f"Items are in escrow and will be returned if not responded to by "
            f"{expires_at.isoformat()}."
        ),
    }
