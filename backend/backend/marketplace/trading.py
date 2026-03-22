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
import uuid as _uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.inventory import add_to_inventory, remove_from_inventory
from backend.models.agent import Agent
from backend.models.inventory import InventoryItem
from backend.models.marketplace import Trade
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def propose_trade(
    db: AsyncSession,
    agent: Agent,
    target_agent_name: str,
    offer_items: list[dict],
    request_items: list[dict],
    offer_money: Decimal,
    request_money: Decimal,
    clock: "Clock",
    settings: "Settings",
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
        raise ValueError(
            "Trade must include at least one offered or requested item or currency"
        )

    # Find target agent
    target_result = await db.execute(
        select(Agent).where(Agent.name == target_agent_name)
    )
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
            select(InventoryItem).where(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.good_slug == slug,
            ).with_for_update()
        )
        inv_item = inv_result.scalar_one_or_none()
        have = inv_item.quantity if inv_item else 0
        if have < qty:
            raise ValueError(
                f"Insufficient inventory: have {have}x {slug!r}, need {qty}"
            )

    # Re-lock agent row before balance modification (prevent double-spend).
    # populate_existing=True forces SQLAlchemy to overwrite the cached identity
    # map entry with fresh data from the DB after the lock is acquired.
    agent_row = await db.execute(
        select(Agent).where(Agent.id == agent.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    agent = agent_row.scalar_one()

    # Verify proposer has enough balance for offer_money
    agent_balance = Decimal(str(agent.balance))
    if offer_money > agent_balance:
        raise ValueError(
            f"Insufficient balance: need {float(offer_money):.2f} "
            f"but have {float(agent_balance):.2f}"
        )

    # Lock offered goods in escrow (remove from proposer's inventory)
    for item in offer_items:
        await remove_from_inventory(db, "agent", agent.id, item["good_slug"], item["quantity"])

    # Lock offered money (deduct from proposer's balance)
    if offer_money > 0:
        agent.balance = agent_balance - offer_money
        await db.flush()

    # Calculate expiry
    timeout_seconds = getattr(settings.economy, "trade_escrow_timeout_seconds",
                              getattr(settings.economy, "trade_escrow_timeout", 3600))
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


async def respond_trade(
    db: AsyncSession,
    agent: Agent,
    trade_id: str,
    accept: bool,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Respond to a pending trade proposal as the target agent.

    If accepting:
      - Verify target has the requested items and money
      - Transfer goods and money between both parties
      - Record transactions with type="trade" (NOT "marketplace")

    If rejecting:
      - Return escrowed items/money to proposer
      - Set status="rejected"

    Args:
        db:       Active async database session.
        agent:    The target agent responding.
        trade_id: UUID string of the trade to respond to.
        accept:   True to accept, False to reject.
        clock:    Clock for current time check.
        settings: Application settings.

    Returns:
        Dict confirming the response.

    Raises:
        ValueError: If trade not found, not target, or already resolved.
    """
    try:
        trade_uuid = _uuid.UUID(trade_id)
    except ValueError:
        raise ValueError(f"Invalid trade ID: {trade_id!r}")

    trade_result = await db.execute(
        select(Trade).where(Trade.id == trade_uuid).with_for_update()
    )
    trade = trade_result.scalar_one_or_none()

    if trade is None:
        raise ValueError(f"Trade {trade_id!r} not found")

    if trade.target_id != agent.id:
        raise ValueError("You are not the target of this trade")

    if trade.status != "pending":
        raise ValueError(f"Trade is no longer pending (status: {trade.status!r})")

    now = clock.now()
    if now >= trade.expires_at:
        raise ValueError(
            f"Trade has expired (expired at {trade.expires_at.isoformat()})"
        )

    # Load and lock both agents to prevent concurrent balance/inventory changes
    agent_row = await db.execute(
        select(Agent).where(Agent.id == agent.id).with_for_update()
    )
    agent = agent_row.scalar_one()

    proposer_result = await db.execute(
        select(Agent).where(Agent.id == trade.proposer_id).with_for_update()
    )
    proposer = proposer_result.scalar_one_or_none()
    if proposer is None:
        raise ValueError("Trade proposer no longer exists")

    if not accept:
        # Reject: return escrowed items/money to proposer
        await _return_escrow_to_proposer(db, trade, proposer, settings)
        trade.status = "rejected"
        trade.escrow_locked = False
        await db.flush()

        logger.info(
            "Trade %s rejected by %s — escrow returned to %s",
            trade_id,
            agent.name,
            proposer.name,
        )

        return {
            "trade_id": trade_id,
            "status": "rejected",
            "message": f"Trade rejected. Proposer's items have been returned.",
        }

    # Accept: verify target has the requested items and money
    request_money = Decimal(str(trade.request_money))
    target_balance = Decimal(str(agent.balance))

    if request_money > target_balance:
        raise ValueError(
            f"Insufficient balance: trade requires {float(request_money):.2f} "
            f"but you have {float(target_balance):.2f}"
        )

    for item in (trade.request_items or []):
        slug = item["good_slug"]
        qty = item["quantity"]
        inv_result = await db.execute(
            select(InventoryItem).where(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.good_slug == slug,
            ).with_for_update()
            .execution_options(populate_existing=True)
        )
        inv_item = inv_result.scalar_one_or_none()
        have = inv_item.quantity if inv_item else 0
        if have < qty:
            raise ValueError(
                f"Insufficient inventory: have {have}x {slug!r}, need {qty}"
            )

    # --- Execute the exchange ---
    # Proposer's offered goods → target (proposer's goods were in escrow)
    for item in (trade.offer_items or []):
        await add_to_inventory(db, "agent", agent.id, item["good_slug"], item["quantity"], settings)

    # Target's requested goods → proposer
    for item in (trade.request_items or []):
        await remove_from_inventory(db, "agent", agent.id, item["good_slug"], item["quantity"])
        await add_to_inventory(db, "agent", proposer.id, item["good_slug"], item["quantity"], settings)

    # Proposer's offered money → target
    offer_money = Decimal(str(trade.offer_money))
    if offer_money > 0:
        agent.balance = target_balance + offer_money

    # Target's requested money → proposer
    if request_money > 0:
        agent.balance = Decimal(str(agent.balance)) - request_money
        proposer.balance = Decimal(str(proposer.balance)) + request_money

    await db.flush()

    # Record transactions with type="trade" (NOT "marketplace" — intentionally not taxed)
    # One transaction for each money transfer
    if offer_money > 0:
        txn_offer_money = Transaction(
            type="trade",
            from_agent_id=proposer.id,
            to_agent_id=agent.id,
            amount=offer_money,
            metadata_json={
                "trade_id": str(trade.id),
                "transfer": "offer_money",
                "offer_items": trade.offer_items,
                "request_items": trade.request_items,
            },
        )
        db.add(txn_offer_money)

    if request_money > 0:
        txn_request_money = Transaction(
            type="trade",
            from_agent_id=agent.id,
            to_agent_id=proposer.id,
            amount=request_money,
            metadata_json={
                "trade_id": str(trade.id),
                "transfer": "request_money",
            },
        )
        db.add(txn_request_money)

    # If no money exchanged but goods did, still record the trade
    if offer_money <= 0 and request_money <= 0:
        if trade.offer_items or trade.request_items:
            txn_barter = Transaction(
                type="trade",
                from_agent_id=proposer.id,
                to_agent_id=agent.id,
                amount=Decimal("0"),
                metadata_json={
                    "trade_id": str(trade.id),
                    "transfer": "barter",
                    "offer_items": trade.offer_items,
                    "request_items": trade.request_items,
                },
            )
            db.add(txn_barter)

    trade.status = "accepted"
    trade.escrow_locked = False
    await db.flush()

    logger.info(
        "Trade %s accepted: %s ↔ %s",
        trade_id,
        proposer.name,
        agent.name,
    )

    return {
        "trade_id": trade_id,
        "status": "accepted",
        "message": f"Trade accepted. Items have been exchanged with {proposer.name!r}.",
        "received_items": trade.offer_items,
        "received_money": float(offer_money),
        "sent_items": trade.request_items,
        "sent_money": float(request_money),
    }


async def cancel_trade(
    db: AsyncSession,
    agent: Agent,
    trade_id: str,
    settings: "Settings",
) -> dict:
    """
    Cancel a pending trade as the proposer.

    Returns escrowed items/money to the proposer.

    Args:
        db:       Active async database session.
        agent:    The proposer cancelling the trade.
        trade_id: UUID string of the trade to cancel.
        settings: Application settings.

    Returns:
        Dict confirming cancellation.

    Raises:
        ValueError: If trade not found, not proposer, or not pending.
    """
    try:
        trade_uuid = _uuid.UUID(trade_id)
    except ValueError:
        raise ValueError(f"Invalid trade ID: {trade_id!r}")

    trade_result = await db.execute(
        select(Trade).where(Trade.id == trade_uuid)
    )
    trade = trade_result.scalar_one_or_none()

    if trade is None:
        raise ValueError(f"Trade {trade_id!r} not found")

    if trade.proposer_id != agent.id:
        raise ValueError("You can only cancel trades you proposed")

    if trade.status != "pending":
        raise ValueError(f"Trade is no longer pending (status: {trade.status!r})")

    # Return escrowed items/money to proposer
    await _return_escrow_to_proposer(db, trade, agent, settings)

    trade.status = "cancelled"
    trade.escrow_locked = False
    await db.flush()

    logger.info("Trade %s cancelled by proposer %s", trade_id, agent.name)

    return {
        "trade_id": trade_id,
        "status": "cancelled",
        "message": "Trade cancelled. Your items have been returned.",
    }


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
            await _return_escrow_to_proposer(db, trade, proposer, settings)

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
            await _return_escrow_to_proposer(db, trade, agent, settings)
        else:
            # Agent is target — return proposer's escrow to them
            proposer_result = await db.execute(
                select(Agent).where(Agent.id == trade.proposer_id)
            )
            proposer = proposer_result.scalar_one_or_none()
            if proposer is not None:
                await _return_escrow_to_proposer(db, trade, proposer, settings)

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


async def _return_escrow_to_proposer(
    db: AsyncSession,
    trade: Trade,
    proposer: Agent,
    settings: "Settings",
) -> None:
    """
    Internal helper: return escrowed items and money to the proposer.

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
