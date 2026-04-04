"""
Trade response and cancellation logic for Agent Economy.

Split from trading.py — handles respond_trade() and cancel_trade().
"""

from __future__ import annotations

import logging
import uuid as _uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.agents.inventory import add_to_inventory, remove_from_inventory
from backend.marketplace.escrow import return_escrow_to_proposer as _return_escrow_to_proposer
from backend.marketplace.locking import lock_agents_in_order
from backend.models.inventory import InventoryItem
from backend.models.marketplace import Trade
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent

logger = logging.getLogger(__name__)


async def respond_trade(
    db: AsyncSession,
    agent: Agent,
    trade_id: str,
    accept: bool,
    clock: Clock,
    settings: Settings,
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

    trade_result = await db.execute(select(Trade).where(Trade.id == trade_uuid).with_for_update())
    trade = trade_result.scalar_one_or_none()

    if trade is None:
        raise ValueError(f"Trade {trade_id!r} not found")

    if trade.target_id != agent.id:
        raise ValueError("You are not the target of this trade")

    if trade.status != "pending":
        raise ValueError(f"Trade is no longer pending (status: {trade.status!r})")

    now = clock.now()
    if now >= trade.expires_at:
        raise ValueError(f"Trade has expired (expired at {trade.expires_at.isoformat()})")

    # Load and lock both agents in UUID order to prevent deadlocks
    responder_id = agent.id
    proposer_id = trade.proposer_id
    locked_agents = await lock_agents_in_order(db, [responder_id, proposer_id])
    agent = locked_agents[responder_id]
    proposer = locked_agents.get(proposer_id)

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
            "message": "Trade rejected. Proposer's items have been returned.",
        }

    # Accept: verify target has the requested items and money
    request_money = Decimal(str(trade.request_money))
    target_balance = Decimal(str(agent.balance))

    if request_money > target_balance:
        raise ValueError(
            f"Insufficient balance: trade requires {float(request_money):.2f} but you have {float(target_balance):.2f}"
        )

    for item in trade.request_items or []:
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
            .execution_options(populate_existing=True)
        )
        inv_item = inv_result.scalar_one_or_none()
        have = inv_item.quantity if inv_item else 0
        if have < qty:
            raise ValueError(f"Insufficient inventory: have {have}x {slug!r}, need {qty}")

    # --- Execute the exchange ---
    # Proposer's offered goods → target (proposer's goods were in escrow)
    for item in trade.offer_items or []:
        await add_to_inventory(db, "agent", agent.id, item["good_slug"], item["quantity"], settings)

    # Target's requested goods → proposer
    for item in trade.request_items or []:
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
    if offer_money <= 0 and request_money <= 0 and (trade.offer_items or trade.request_items):
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
    settings: Settings,
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

    trade_result = await db.execute(select(Trade).where(Trade.id == trade_uuid).with_for_update())
    trade = trade_result.scalar_one_or_none()

    if trade is None:
        raise ValueError(f"Trade {trade_id!r} not found")

    if trade.proposer_id != agent.id:
        raise ValueError("You can only cancel trades you proposed")

    if trade.status != "pending":
        raise ValueError(f"Trade is no longer pending (status: {trade.status!r})")

    proposer = (await lock_agents_in_order(db, [agent.id]))[agent.id]

    # Return escrowed items/money to proposer
    await _return_escrow_to_proposer(db, trade, proposer, settings)

    trade.status = "cancelled"
    trade.escrow_locked = False
    await db.flush()

    logger.info("Trade %s cancelled by proposer %s", trade_id, agent.name)

    return {
        "trade_id": trade_id,
        "status": "cancelled",
        "message": "Trade cancelled. Your items have been returned.",
    }
