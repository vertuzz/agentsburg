"""
Loan operations for Agent Economy banking.

Public functions:
  take_loan()                       — disburse a new loan if credit & reserves allow

Re-exports from loan_admin:
  process_loan_payments()           — collect installments (slow tick / hourly)
  default_agent_loans()             — default all active loans for a bankrupt agent
  close_bank_account_for_bankruptcy() — liquidate bank account during bankruptcy
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.banking._helpers import (
    INSTALLMENT_INTERVAL_HOURS,
    LOAN_INSTALLMENTS,
    _get_active_policy,
    _get_central_bank,
    _round_money,
    _to_decimal,
)

# Re-export admin functions so existing imports from backend.banking.loans still work
from backend.banking.loan_admin import (  # noqa: F401
    close_bank_account_for_bankruptcy,
    default_agent_loans,
    process_loan_payments,
)
from backend.models.agent import Agent
from backend.models.banking import Loan
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def take_loan(
    db: AsyncSession,
    agent: Agent,
    amount: Decimal,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Disburse a new loan to the agent if credit and reserves allow.

    Checks:
      1. amount > 0
      2. amount <= credit.max_loan_amount
      3. Fractional reserve capacity: reserves / reserve_ratio - total_loaned >= amount
      4. No other active loans (one loan at a time per agent)

    If approved:
      agent.balance       += amount
      bank.total_loaned   += amount
      bank.reserves       -= amount  (money leaves the bank)
      Creates Loan with 24 hourly installments

    Installment = (principal * (1 + interest_rate)) / 24
    next_payment_at = clock.now() + 1 hour

    Transaction type: "loan_disbursement"

    Args:
        db:       Active async session.
        agent:    Borrowing agent.
        amount:   Loan principal requested.
        clock:    For scheduling next_payment_at.
        settings: Application settings.

    Returns:
        Dict with loan details and new wallet balance.

    Raises:
        ValueError: for any credit or reserve constraint violation.
    """
    from backend.banking.credit import calculate_credit

    amount = _to_decimal(amount)
    if amount <= 0:
        raise ValueError("Loan amount must be positive")
    if amount < Decimal("1"):
        raise ValueError("Minimum loan amount is 1")

    if agent.bankruptcy_count >= 3:
        raise ValueError("Loan denied: too many prior bankruptcies (3+). Your credit history is too poor.")

    # Lock agent row to prevent concurrent loan/balance manipulation
    agent_row = await db.execute(select(Agent).where(Agent.id == agent.id).with_for_update())
    agent = agent_row.scalar_one()

    # --- Check credit ---
    credit = await calculate_credit(db, agent, clock, settings)
    max_loan = _to_decimal(credit["max_loan_amount"])

    if amount > max_loan:
        raise ValueError(
            f"Requested amount {float(amount):.2f} exceeds your credit limit "
            f"{float(max_loan):.2f} (credit score: {credit['credit_score']})"
        )

    if max_loan <= 0:
        raise ValueError(
            "Your credit score does not qualify for any loan. Build net worth, avoid bankruptcies, and try again."
        )

    # --- Check one-loan-at-a-time (locked to prevent double-loan race) ---
    existing_result = await db.execute(
        select(Loan)
        .where(
            Loan.agent_id == agent.id,
            Loan.status == "active",
        )
        .with_for_update()
    )
    existing_loan = existing_result.scalar_one_or_none()
    if existing_loan is not None:
        raise ValueError(
            f"You already have an active loan with {existing_loan.installments_remaining} "
            f"installments remaining. Repay it before taking a new loan."
        )

    # --- Check fractional reserve capacity ---
    bank = await _get_central_bank(db, lock=True)
    policy = await _get_active_policy(db, settings)

    reserves = _to_decimal(bank.reserves)
    total_loaned = _to_decimal(bank.total_loaned)
    reserve_ratio = Decimal(str(policy.get("reserve_ratio", settings.economy.default_reserve_ratio)))

    if reserve_ratio <= 0:
        reserve_ratio = Decimal("0.01")  # Safety floor

    # Fractional reserve: bank can lend reserves / reserve_ratio total
    # capacity = reserves / reserve_ratio - total_loaned
    lending_capacity = _round_money(reserves / reserve_ratio - total_loaned)

    # No single agent can borrow more than 10% of bank reserves
    max_from_reserves = _round_money(reserves * Decimal("0.10"))
    effective_max = min(max_loan, lending_capacity, max_from_reserves)

    if amount > effective_max:
        raise ValueError(
            f"Requested amount {float(amount):.2f} exceeds effective limit "
            f"{float(effective_max):.2f} (credit limit: {float(max_loan):.2f}, "
            f"lending capacity: {float(lending_capacity):.2f}, "
            f"max from reserves: {float(max_from_reserves):.2f})"
        )

    # --- Create the loan ---
    interest_rate = credit["interest_rate"]
    total_repayment = amount * (Decimal("1") + Decimal(str(interest_rate)))
    installment_amount = _round_money(total_repayment / Decimal(str(LOAN_INSTALLMENTS)))
    next_payment_at = clock.now() + timedelta(hours=INSTALLMENT_INTERVAL_HOURS)

    loan = Loan(
        agent_id=agent.id,
        principal=amount,
        remaining_balance=_round_money(total_repayment),
        interest_rate=interest_rate,
        installment_amount=installment_amount,
        installments_remaining=LOAN_INSTALLMENTS,
        next_payment_at=next_payment_at,
        status="active",
    )
    db.add(loan)

    # --- Update agent and bank ---
    agent.balance = _round_money(_to_decimal(agent.balance) + amount)
    bank.total_loaned = _round_money(total_loaned + amount)
    bank.reserves = _round_money(reserves - amount)

    # Transaction record
    txn = Transaction(
        type="loan_disbursement",
        from_agent_id=None,  # from bank
        to_agent_id=agent.id,
        amount=amount,
        metadata_json={
            "interest_rate": interest_rate,
            "installments": LOAN_INSTALLMENTS,
            "installment_amount": float(installment_amount),
            "total_repayment": float(total_repayment),
            "next_payment_at": next_payment_at.isoformat(),
            "tick_time": clock.now().isoformat(),
        },
    )
    db.add(txn)
    await db.flush()

    logger.info(
        "Loan disbursed: agent=%s amount=%.2f rate=%.4f installment=%.2f",
        agent.name,
        float(amount),
        interest_rate,
        float(installment_amount),
    )

    return {
        "action": "take_loan",
        "principal": float(amount),
        "interest_rate": interest_rate,
        "total_repayment": float(total_repayment),
        "installment_amount": float(installment_amount),
        "installments_remaining": LOAN_INSTALLMENTS,
        "next_payment_at": next_payment_at.isoformat(),
        "wallet_balance": float(agent.balance),
        "loan_id": str(loan.id),
    }
