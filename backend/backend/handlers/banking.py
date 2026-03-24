"""Banking handler: deposits, withdrawals, loans, balance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.errors import (
    ALREADY_EXISTS,
    INSUFFICIENT_FUNDS,
    INVALID_PARAMS,
    NOT_ELIGIBLE,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_bank(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Banking operations: deposit, withdraw, take a loan, or view your balance.

    action='deposit':
      Move money from your wallet into your bank account.
      Bank accounts earn interest on deposits (hourly slow tick).
      Requires: amount > 0 and wallet balance >= amount.

    action='withdraw':
      Move money from your bank account back to your wallet.
      Requires: amount > 0 and account balance >= amount.

    action='take_loan':
      Borrow money from the central bank (fractional reserve lending).
      Loan amount and interest rate depend on your credit score.
      Repaid in 24 hourly installments (deducted automatically).
      Defaulting triggers bankruptcy. Only one active loan at a time.
      Requires: credit score > 0, bank has capacity, amount <= credit limit.

    action='view_balance':
      Show your bank account balance, active loans, and current credit score.
      Credit score determines your borrowing limit and interest rate.
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    action = params.get("action")
    valid_actions = ("deposit", "withdraw", "take_loan", "view_balance")
    if action not in valid_actions:
        raise ToolError(
            INVALID_PARAMS,
            f"Parameter 'action' must be one of: {', '.join(valid_actions)}",
        )

    from decimal import Decimal as _Decimal

    from backend.banking.service import deposit, take_loan, view_balance, withdraw
    from backend.hints import get_pending_events

    if action == "view_balance":
        result = await view_balance(db, agent, clock, settings)
        pending_events = await get_pending_events(db, agent)
        result["_hints"] = {"pending_events": pending_events, "check_back_seconds": 3600}
        return result

    # All other actions require 'amount'
    raw_amount = params.get("amount")
    if raw_amount is None:
        raise ToolError(
            INVALID_PARAMS,
            f"Parameter 'amount' is required for action='{action}'",
        )
    try:
        amount = _Decimal(str(raw_amount))
    except Exception:
        raise ToolError(INVALID_PARAMS, "Parameter 'amount' must be a number")

    if amount <= 0:
        raise ToolError(INVALID_PARAMS, "Parameter 'amount' must be greater than 0")

    if action == "deposit":
        try:
            result = await deposit(db, agent, amount, clock)
        except ValueError as e:
            error_msg = str(e)
            if "insufficient" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 3600,
                "message": (
                    f"Deposited {float(amount):.2f}. Your account now earns interest. "
                    f"Withdraw any time. Account balance: {result['account_balance']:.2f}"
                ),
            },
        }

    elif action == "withdraw":
        try:
            result = await withdraw(db, agent, amount, clock)
        except ValueError as e:
            error_msg = str(e)
            if "insufficient" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 60,
                "message": (
                    f"Withdrew {float(amount):.2f} to your wallet. Wallet balance: {result['wallet_balance']:.2f}"
                ),
            },
        }

    else:  # take_loan
        try:
            result = await take_loan(db, agent, amount, clock, settings)
        except ValueError as e:
            error_msg = str(e)
            if ("credit" in error_msg.lower() and "limit" in error_msg.lower()) or (
                "credit score" in error_msg.lower() and "not qualify" in error_msg.lower()
            ):
                raise ToolError(NOT_ELIGIBLE, error_msg) from e
            if "active loan" in error_msg.lower():
                raise ToolError(ALREADY_EXISTS, error_msg) from e
            if "capacity" in error_msg.lower():
                raise ToolError(INSUFFICIENT_FUNDS, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        # Spectator feed: loan disbursed
        try:
            from backend.spectator.events import emit_spectator_event

            await emit_spectator_event(
                redis,
                "loan_disbursed",
                {
                    "agent_name": agent.name,
                    "amount": result.get("principal", amount),
                    "interest_rate": result.get("interest_rate", 0),
                },
                clock,
                "notable",
            )
        except Exception:
            pass  # Non-critical

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 3600,
                "message": (
                    f"Loan of {result['principal']:.2f} disbursed. "
                    f"Installments: {result['installments_remaining']}x {result['installment_amount']:.2f} "
                    f"due hourly. First payment: {result['next_payment_at']}. "
                    f"Missing a payment triggers bankruptcy."
                ),
            },
        }
