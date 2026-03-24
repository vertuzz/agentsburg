"""
Loan administration — installment collection, defaults, and bankruptcy account closure.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.banking._helpers import (
    INSTALLMENT_INTERVAL_HOURS,
    _get_central_bank,
    _round_money,
    _to_decimal,
)
from backend.models.agent import Agent
from backend.models.banking import BankAccount, Loan
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)


async def process_loan_payments(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """Collect installments from all agents with active, due loans (slow tick)."""
    now = clock.now()

    result = await db.execute(select(Loan).where(Loan.status == "active", Loan.next_payment_at <= now))
    due_loans = list(result.scalars().all())

    if not due_loans:
        return {"type": "loan_payments", "processed": 0, "paid": 0, "defaulted": 0, "total_collected": 0.0}

    agent_ids = {loan.agent_id for loan in due_loans}
    agents_result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)).with_for_update())
    agents_by_id = {a.id: a for a in agents_result.scalars().all()}

    bank = await _get_central_bank(db, lock=True)
    reserves = _to_decimal(bank.reserves)
    total_loaned = _to_decimal(bank.total_loaned)

    paid_count = 0
    defaulted_count = 0
    total_collected = Decimal("0")
    bankruptcy_threshold = Decimal(str(settings.economy.bankruptcy_debt_threshold))

    for loan in due_loans:
        agent = agents_by_id.get(loan.agent_id)
        if agent is None:
            logger.warning("Loan %s has no corresponding agent — skipping", loan.id)
            continue

        # Final installment: pay exact remaining balance to avoid rounding drift
        installment = (
            _to_decimal(loan.remaining_balance)
            if loan.installments_remaining == 1
            else _to_decimal(loan.installment_amount)
        )
        agent_balance = _to_decimal(agent.balance)

        if agent_balance >= installment:
            agent.balance = _round_money(agent_balance - installment)
            remaining = _to_decimal(loan.remaining_balance) - installment
            loan.remaining_balance = _round_money(max(Decimal("0"), remaining))
            loan.installments_remaining = max(0, loan.installments_remaining - 1)
            reserves += installment
            total_loaned -= installment
            total_collected += installment

            if loan.installments_remaining == 0:
                loan.status = "paid_off"
                logger.info("Loan %s fully paid off by agent %s", loan.id, agent.name)
            else:
                loan.next_payment_at = now + timedelta(hours=INSTALLMENT_INTERVAL_HOURS)

            paid_count += 1
            db.add(
                Transaction(
                    type="loan_payment",
                    from_agent_id=agent.id,
                    to_agent_id=None,
                    amount=installment,
                    metadata_json={
                        "loan_id": str(loan.id),
                        "installments_remaining": loan.installments_remaining,
                        "tick_time": now.isoformat(),
                    },
                )
            )
        else:
            logger.warning(
                "Agent %s defaulted on loan %s (balance: %.2f, installment: %.2f)",
                agent.name,
                loan.id,
                float(agent_balance),
                float(installment),
            )
            remaining_principal = _to_decimal(loan.remaining_balance)
            total_loaned -= remaining_principal
            loan.status = "defaulted"
            loan.remaining_balance = Decimal("0")
            agent.balance = _round_money(bankruptcy_threshold - Decimal("1"))
            defaulted_count += 1
            db.add(
                Transaction(
                    type="loan_payment",
                    from_agent_id=agent.id,
                    to_agent_id=None,
                    amount=Decimal("0"),
                    metadata_json={
                        "loan_id": str(loan.id),
                        "status": "defaulted",
                        "remaining_written_off": float(remaining_principal),
                        "tick_time": now.isoformat(),
                    },
                )
            )

    bank.reserves = _round_money(reserves)
    bank.total_loaned = _round_money(max(Decimal("0"), total_loaned))
    await db.flush()

    logger.info(
        "Loan payments: %d processed (%d paid, %d defaulted), collected %.2f",
        len(due_loans),
        paid_count,
        defaulted_count,
        float(total_collected),
    )
    return {
        "type": "loan_payments",
        "processed": len(due_loans),
        "paid": paid_count,
        "defaulted": defaulted_count,
        "total_collected": float(total_collected),
    }


async def default_agent_loans(
    db: AsyncSession,
    agent: Agent,
    clock: Clock,
) -> dict:
    """Default all active loans for a bankrupt agent, writing off remaining balances."""
    now = clock.now()

    result = await db.execute(select(Loan).where(Loan.agent_id == agent.id, Loan.status == "active"))
    active_loans = list(result.scalars().all())

    if not active_loans:
        return {"loans_defaulted": 0, "total_written_off": 0.0}

    bank = await _get_central_bank(db)
    total_loaned = _to_decimal(bank.total_loaned)
    total_written_off = Decimal("0")

    for loan in active_loans:
        remaining = _to_decimal(loan.remaining_balance)
        total_written_off += remaining
        total_loaned -= remaining
        loan.status = "defaulted"
        loan.remaining_balance = Decimal("0")
        db.add(
            Transaction(
                type="loan_payment",
                from_agent_id=agent.id,
                to_agent_id=None,
                amount=Decimal("0"),
                metadata_json={
                    "loan_id": str(loan.id),
                    "status": "defaulted_bankruptcy",
                    "remaining_written_off": float(remaining),
                    "tick_time": now.isoformat(),
                },
            )
        )

    bank.total_loaned = _round_money(max(Decimal("0"), total_loaned))
    await db.flush()

    logger.info(
        "Defaulted %d loans for bankrupt agent %s, wrote off %.2f",
        len(active_loans),
        agent.name,
        float(total_written_off),
    )
    return {
        "loans_defaulted": len(active_loans),
        "total_written_off": float(total_written_off),
    }


async def close_bank_account_for_bankruptcy(
    db: AsyncSession,
    agent: Agent,
    clock: Clock,
) -> dict:
    """
    Liquidate bank account during bankruptcy, seizing deposits to repay loans first.

    Prevents the exploit: take loan -> deposit everything -> default -> recover deposits.
    """
    now = clock.now()

    result = await db.execute(select(BankAccount).where(BankAccount.agent_id == agent.id))
    account = result.scalar_one_or_none()
    deposit_balance = _to_decimal(account.balance) if account else Decimal("0")

    loans_result = await db.execute(select(Loan).where(Loan.agent_id == agent.id, Loan.status == "active"))
    active_loans = list(loans_result.scalars().all())

    bank = await _get_central_bank(db)
    total_loaned = _to_decimal(bank.total_loaned)

    applied_to_loans = Decimal("0")
    written_off = Decimal("0")
    remaining_deposits = deposit_balance

    for loan in active_loans:
        loan_remaining = _to_decimal(loan.remaining_balance)
        if remaining_deposits >= loan_remaining:
            applied_to_loans += loan_remaining
            remaining_deposits -= loan_remaining
            total_loaned -= loan_remaining
            loan.status = "defaulted"
            loan.remaining_balance = Decimal("0")
            db.add(
                Transaction(
                    type="loan_payment",
                    from_agent_id=agent.id,
                    to_agent_id=None,
                    amount=loan_remaining,
                    metadata_json={
                        "loan_id": str(loan.id),
                        "status": "bankruptcy_seized_deposits",
                        "tick_time": now.isoformat(),
                    },
                )
            )
        else:
            if remaining_deposits > 0:
                applied_to_loans += remaining_deposits
                loan_remaining -= remaining_deposits
                total_loaned -= remaining_deposits
                db.add(
                    Transaction(
                        type="loan_payment",
                        from_agent_id=agent.id,
                        to_agent_id=None,
                        amount=remaining_deposits,
                        metadata_json={
                            "loan_id": str(loan.id),
                            "status": "bankruptcy_partial_seizure",
                            "partial_amount": float(remaining_deposits),
                            "tick_time": now.isoformat(),
                        },
                    )
                )
                remaining_deposits = Decimal("0")

            written_off += loan_remaining
            total_loaned -= loan_remaining
            loan.status = "defaulted"
            loan.remaining_balance = Decimal("0")
            db.add(
                Transaction(
                    type="loan_payment",
                    from_agent_id=agent.id,
                    to_agent_id=None,
                    amount=Decimal("0"),
                    metadata_json={
                        "loan_id": str(loan.id),
                        "status": "defaulted_bankruptcy",
                        "remaining_written_off": float(loan_remaining),
                        "tick_time": now.isoformat(),
                    },
                )
            )

    if account is not None:
        account.balance = Decimal("0")

    # Remaining deposits (after loan repayment) go back to agent wallet
    if remaining_deposits > 0:
        agent.balance = _round_money(_to_decimal(agent.balance) + remaining_deposits)
        bank.reserves = _round_money(_to_decimal(bank.reserves) - remaining_deposits)
        db.add(
            Transaction(
                type="withdrawal",
                from_agent_id=None,
                to_agent_id=agent.id,
                amount=remaining_deposits,
                metadata_json={
                    "reason": "bankruptcy_account_remainder",
                    "tick_time": now.isoformat(),
                },
            )
        )

    bank.total_loaned = _round_money(max(Decimal("0"), total_loaned))
    await db.flush()

    logger.info(
        "Bankruptcy account closure for agent %s: deposits=%.2f, applied_to_loans=%.2f, "
        "written_off=%.2f, returned_to_wallet=%.2f",
        agent.name,
        float(deposit_balance),
        float(applied_to_loans),
        float(written_off),
        float(remaining_deposits),
    )
    return {
        "account_balance_recovered": float(remaining_deposits),
        "deposits_applied_to_loans": float(applied_to_loans),
        "loan_debt_written_off": float(written_off),
    }
