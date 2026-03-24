"""
Banking models for Agent Economy.

Three tables:
  BankAccount  — one per agent, holds deposit balance separate from wallet
  Loan         — installment loans with credit-scored terms
  CentralBank  — singleton that tracks total reserves and outstanding loans

Money supply identity:
  sum(agent.balance) + sum(bank_account.balance) + escrow_locked + market_order_locked
    = initial_reserves + total_loans_created - total_loans_repaid

The CentralBank implements fractional reserve banking:
  lending_capacity = reserves / reserve_ratio - total_loaned

This means with reserve_ratio=0.10, the bank can lend 10x its reserves.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from datetime import datetime


class BankAccount(TimestampMixin, Base):
    """
    A bank deposit account for an agent.

    One account per agent (unique constraint on agent_id).
    The balance here is SEPARATE from agent.balance (wallet).

    Deposit earns interest; the bank uses these deposits to fund loans
    (fractional reserve — only a fraction needs to stay as reserves).
    """

    __tablename__ = "bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    # One account per agent — enforced via unique constraint
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Deposit balance — separate from agent's wallet balance
    balance: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<BankAccount agent={self.agent_id} balance={self.balance}>"


class Loan(TimestampMixin, Base):
    """
    An installment loan from the central bank to an agent.

    Loans are repaid in 24 equal installments (hourly in simulation time).
    The total repayment = principal * (1 + interest_rate).
    Each installment = total_repayment / 24.

    If the agent cannot pay an installment, the loan defaults
    and the agent goes into the bankruptcy pipeline.

    Statuses:
      active   — installments are still being collected
      paid_off — all installments paid, loan closed
      defaulted — agent failed to pay, bankruptcy triggered
    """

    __tablename__ = "loans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Original principal lent
    principal: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)

    # Outstanding balance (decrements with each payment)
    remaining_balance: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)

    # Annual interest rate (e.g., 0.05 = 5%). Per-installment rate applied at disbursement.
    interest_rate: Mapped[float] = mapped_column(Float, nullable=False)

    # Fixed installment amount = (principal * (1 + interest_rate)) / 24
    installment_amount: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)

    # How many installments remain (starts at 24, counts down to 0)
    installments_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=24)

    # When the next installment is due
    next_payment_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # active | paid_off | defaulted
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    def __repr__(self) -> str:
        return (
            f"<Loan agent={self.agent_id} principal={self.principal} "
            f"remaining={self.remaining_balance} status={self.status!r}>"
        )


class CentralBank(Base):
    """
    Singleton central bank record.

    Always has id=1. Created once during bootstrap; never deleted.

    reserves      — actual money held by the bank (from initial seed + tax revenue
                    + loan repayments - deposits paid out - loans disbursed)
    total_loaned  — sum of all outstanding loan principal (remaining_balance across
                    all active loans)

    Fractional reserve constraint:
      At any moment: total_loaned <= reserves / reserve_ratio

    This means with reserves=100_000 and reserve_ratio=0.10, the bank
    can have up to 1,000,000 outstanding loans. New lending capacity:
      capacity = reserves / reserve_ratio - total_loaned
    """

    __tablename__ = "central_bank"

    # Singleton — always id=1
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Actual money held in reserve
    reserves: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # Total outstanding loan principal across all active loans
    total_loaned: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<CentralBank reserves={self.reserves} total_loaned={self.total_loaned}>"
