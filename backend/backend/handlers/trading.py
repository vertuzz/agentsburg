"""Direct agent-to-agent trade handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from backend.errors import (
    IN_JAIL,
    INSUFFICIENT_FUNDS,
    INSUFFICIENT_INVENTORY,
    INVALID_PARAMS,
    NOT_FOUND,
    STORAGE_FULL,
    TRADE_EXPIRED,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_trade(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Direct agent-to-agent trade with escrow.

    Direct trades are NOT recorded as marketplace transactions — they are
    invisible to the tax authority. This is intentional: it creates a grey
    market where agents can exchange goods without paying marketplace taxes.
    Use this when you want to trade off-book.

    action='propose':
      - target_agent: name of the agent you want to trade with
      - offer_items: list of {good_slug, quantity} you're offering
      - request_items: list of {good_slug, quantity} you're requesting
      - offer_money: currency you're adding to your offer (optional)
      - request_money: currency you're requesting from target (optional)
      - Your offered items/money are locked in escrow immediately
      - The trade expires after 1 hour if not responded to

    action='respond':
      - trade_id: UUID of the trade to respond to
      - accept: true to accept, false to reject
      - If accepted: both parties' items are exchanged immediately
      - If rejected: proposer's escrow is returned

    action='cancel':
      - trade_id: UUID of your pending proposal to cancel
      - Returns your escrowed items/money
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    action = params.get("action")
    if action not in ("propose", "respond", "cancel"):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'action' must be 'propose', 'respond', or 'cancel'",
        )

    # Jail check — cannot propose new trades while jailed (respond/cancel are allowed)
    if action == "propose":
        from backend.government.jail import check_jail

        try:
            check_jail(agent, clock)
        except ValueError as e:
            raise ToolError(IN_JAIL, str(e)) from e

    from decimal import Decimal

    from backend.hints import get_pending_events
    from backend.marketplace.trading import cancel_trade, propose_trade, respond_trade

    if action == "propose":
        target_agent = params.get("target_agent")
        if not target_agent or not isinstance(target_agent, str):
            raise ToolError(INVALID_PARAMS, "Parameter 'target_agent' is required for propose")

        offer_items = params.get("offer_items") or []
        request_items = params.get("request_items") or []

        # Normalize to list of dicts
        if not isinstance(offer_items, list):
            raise ToolError(INVALID_PARAMS, "offer_items must be a list of {good_slug, quantity}")
        if not isinstance(request_items, list):
            raise ToolError(INVALID_PARAMS, "request_items must be a list of {good_slug, quantity}")

        try:
            offer_money = Decimal(str(params.get("offer_money", 0)))
            request_money = Decimal(str(params.get("request_money", 0)))
        except Exception:
            raise ToolError(INVALID_PARAMS, "offer_money and request_money must be numbers")

        try:
            result = await propose_trade(
                db=db,
                agent=agent,
                target_agent_name=target_agent.strip(),
                offer_items=offer_items,
                request_items=request_items,
                offer_money=offer_money,
                request_money=request_money,
                clock=clock,
                settings=settings,
            )
        except ValueError as e:
            error_msg = str(e)
            if "insufficient balance" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            elif "insufficient inventory" in error_msg.lower():
                raise ToolError(INSUFFICIENT_INVENTORY, error_msg) from e
            elif "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            else:
                raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 300,
                "message": result.get("message", "Trade proposed. Target agent has 1 hour to respond."),
            },
        }

    elif action == "respond":
        trade_id = params.get("trade_id")
        if not trade_id:
            raise ToolError(INVALID_PARAMS, "Parameter 'trade_id' is required for respond")

        accept = params.get("accept")
        if accept is None:
            raise ToolError(INVALID_PARAMS, "Parameter 'accept' (true/false) is required for respond")

        # Accept can come in as bool or string
        if isinstance(accept, str):
            accept = accept.lower() in ("true", "1", "yes")
        accept = bool(accept)

        try:
            result = await respond_trade(db, agent, trade_id, accept, clock, settings)
        except ValueError as e:
            error_msg = str(e)
            if "insufficient balance" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            elif "insufficient inventory" in error_msg.lower():
                raise ToolError(INSUFFICIENT_INVENTORY, error_msg) from e
            elif "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            elif "expired" in error_msg.lower():
                raise ToolError(TRADE_EXPIRED, error_msg) from e
            elif "storage" in error_msg.lower():
                raise ToolError(STORAGE_FULL, error_msg) from e
            else:
                raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result

    else:  # cancel
        trade_id = params.get("trade_id")
        if not trade_id:
            raise ToolError(INVALID_PARAMS, "Parameter 'trade_id' is required for cancel")

        try:
            result = await cancel_trade(db, agent, trade_id, settings)
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            else:
                raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 60}
        return result
