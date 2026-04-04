"""
Deposit and withdrawal operations for Agent Economy banking.

Public functions:
  deposit()     — move money from agent wallet to bank account
  withdraw()    — move money from bank account to agent wallet
  view_balance() — return account snapshot
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.banking._helpers import (
    _get_active_policy,
    _get_central_bank,
    _get_or_create_account,
    _round_money,
    _to_decimal,
    lock_agent_for_update,
)
from backend.models.banking import BankAccount, Loan
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent

logger = logging.getLogger(__name__)


async def deposit(
    db: AsyncSession,
    agent: Agent,
    amount: Decimal,
    clock: Clock,
) -> dict:
    """
    Deposit money from the agent's wallet into their bank account.

    Flow:
      agent.balance  -= amount
      account.balance += amount
      central_bank.reserves += amount  (bank now holds those funds)

    Transaction type: "deposit"

    Args:
        db:     Active async session.
        agent:  The depositing agent.
        amount: Amount to deposit (must be > 0, agent must have enough).
        clock:  For transaction timestamp.

    Returns:
        Dict with new wallet and account balances.

    Raises:
        ValueError: if amount <= 0 or agent has insufficient wallet balance.
    """
    amount = _to_decimal(amount)
    if amount <= 0:
        raise ValueError("Deposit amount must be positive")

    # Lock agent row, bank account, and central bank to prevent concurrent mutations
    agent = await lock_agent_for_update(db, agent.id)

    agent_balance = _to_decimal(agent.balance)
    if agent_balance < amount:
        raise ValueError(f"Insufficient wallet balance: have {float(agent_balance):.2f}, need {float(amount):.2f}")

    account = await _get_or_create_account(db, agent, lock=True)
    bank = await _get_central_bank(db, lock=True)

    # Move money: wallet → bank account
    agent.balance = _round_money(agent_balance - amount)
    account.balance = _round_money(_to_decimal(account.balance) + amount)
    bank.reserves = _round_money(_to_decimal(bank.reserves) + amount)

    txn = Transaction(
        type="deposit",
        from_agent_id=agent.id,
        to_agent_id=None,  # into bank system
        amount=amount,
        metadata_json={
            "new_wallet_balance": float(agent.balance),
            "new_account_balance": float(account.balance),
            "tick_time": clock.now().isoformat(),
        },
    )
    db.add(txn)
    await db.flush()

    logger.info(
        "Agent %s deposited %.2f (account now %.2f, wallet now %.2f)",
        agent.name,
        float(amount),
        float(account.balance),
        float(agent.balance),
    )

    return {
        "action": "deposit",
        "amount_deposited": float(amount),
        "wallet_balance": float(agent.balance),
        "account_balance": float(account.balance),
    }


async def withdraw(
    db: AsyncSession,
    agent: Agent,
    amount: Decimal,
    clock: Clock,
) -> dict:
    """
    Withdraw money from the agent's bank account to their wallet.

    Flow:
      account.balance -= amount
      agent.balance   += amount
      central_bank.reserves -= amount

    Transaction type: "withdrawal"

    Args:
        db:     Active async session.
        agent:  The withdrawing agent.
        amount: Amount to withdraw (must be > 0, account must have enough).
        clock:  For transaction timestamp.

    Returns:
        Dict with new wallet and account balances.

    Raises:
        ValueError: if amount <= 0, no account exists, or insufficient account balance.
    """
    amount = _to_decimal(amount)
    if amount <= 0:
        raise ValueError("Withdrawal amount must be positive")

    # Lock agent row, bank account, and central bank to prevent concurrent mutations
    agent = await lock_agent_for_update(db, agent.id)

    account = await _get_or_create_account(db, agent, lock=True)
    account_balance = _to_decimal(account.balance)

    if account_balance < amount:
        raise ValueError(f"Insufficient account balance: have {float(account_balance):.2f}, need {float(amount):.2f}")

    bank = await _get_central_bank(db, lock=True)
    reserves = _to_decimal(bank.reserves)

    # Sanity check: reserves should cover withdrawal (they always should if money supply is conserved)
    if reserves < amount:
        raise ValueError(
            f"Bank reserves insufficient to process withdrawal: "
            f"reserves={float(reserves):.2f}, requested={float(amount):.2f}"
        )

    # Move money: bank account → wallet
    account.balance = _round_money(account_balance - amount)
    agent.balance = _round_money(_to_decimal(agent.balance) + amount)
    bank.reserves = _round_money(reserves - amount)

    txn = Transaction(
        type="withdrawal",
        from_agent_id=None,  # from bank system
        to_agent_id=agent.id,
        amount=amount,
        metadata_json={
            "new_wallet_balance": float(agent.balance),
            "new_account_balance": float(account.balance),
            "tick_time": clock.now().isoformat(),
        },
    )
    db.add(txn)
    await db.flush()

    logger.info(
        "Agent %s withdrew %.2f (account now %.2f, wallet now %.2f)",
        agent.name,
        float(amount),
        float(account.balance),
        float(agent.balance),
    )

    return {
        "action": "withdraw",
        "amount_withdrawn": float(amount),
        "wallet_balance": float(agent.balance),
        "account_balance": float(account.balance),
    }


async def view_balance(
    db: AsyncSession,
    agent: Agent,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Return a full banking snapshot for the agent.

    Includes:
      - Wallet balance
      - Bank account balance
      - Active loans (remaining balance, installment info, next payment)
      - Credit score and current loan terms available
      - Bank stats (reserves, lending capacity)

    Args:
        db:       Active async session.
        agent:    Agent whose banking info to show.
        clock:    For credit calculation age computation.
        settings: Application settings.

    Returns:
        Dict with full banking snapshot.
    """
    from backend.banking.credit import calculate_credit

    account = await _get_or_create_account(db, agent)
    account_balance = _to_decimal(account.balance)

    # Active loans
    loans_result = await db.execute(
        select(Loan).where(
            Loan.agent_id == agent.id,
            Loan.status == "active",
        )
    )
    active_loans = loans_result.scalars().all()

    loan_data = []
    for loan in active_loans:
        loan_data.append(
            {
                "loan_id": str(loan.id),
                "principal": float(loan.principal),
                "remaining_balance": float(loan.remaining_balance),
                "interest_rate": loan.interest_rate,
                "installment_amount": float(loan.installment_amount),
                "installments_remaining": loan.installments_remaining,
                "next_payment_at": loan.next_payment_at.isoformat(),
                "status": loan.status,
            }
        )

    # Credit scoring
    credit = await calculate_credit(db, agent, clock, settings)

    # Bank stats
    bank = await _get_central_bank(db)
    policy = await _get_active_policy(db, settings)
    reserve_ratio = Decimal(str(policy.get("reserve_ratio", settings.economy.default_reserve_ratio)))
    reserves = _to_decimal(bank.reserves)
    total_loaned = _to_decimal(bank.total_loaned)
    lending_capacity = _round_money(reserves / reserve_ratio - total_loaned)

    return {
        "wallet_balance": float(agent.balance),
        "account_balance": float(account_balance),
        "total_wealth": float(_to_decimal(agent.balance) + account_balance),
        "active_loans": loan_data,
        "credit": credit,
        "bank_info": {
            "reserves": float(bank.reserves),
            "total_loaned": float(bank.total_loaned),
            "lending_capacity": float(max(Decimal("0"), lending_capacity)),
            "reserve_ratio": float(reserve_ratio),
        },
        "_hints": {
            "message": (
                "Deposit earns interest. Loans require installment payments every hour. "
                "Defaulting on a loan triggers bankruptcy."
            ),
            "deposit_interest_rate_annual": float(settings.economy.deposit_interest_rate),
        },
    }


async def process_deposit_interest(
    db: AsyncSession,
    clock: Clock,
    settings: Settings,
) -> dict:
    """
    Pay interest on all bank deposits above the minimum threshold.

    Called every hour (slow tick). Interest rate is annual; we compute
    the hourly rate:
        hourly_rate = deposit_interest_rate / 8760  (hours per year)

    Interest is paid from CentralBank.reserves into account.balance.
    This is NOT new money creation — it just moves from reserves to accounts.

    Accounts below min_deposit_for_interest earn nothing (prevents micro-farming).

    Transaction type: "deposit_interest"

    Returns:
        Summary dict with count of accounts receiving interest, total paid.
    """
    now = clock.now()

    # Hourly deposit interest rate
    annual_rate = Decimal(str(settings.economy.deposit_interest_rate))
    hourly_rate = annual_rate / Decimal("8760")  # 8760 hours per year

    min_balance = Decimal(str(settings.economy.min_deposit_for_interest))

    # Load all accounts with meaningful balances
    result = await db.execute(select(BankAccount).where(BankAccount.balance >= min_balance))
    accounts = list(result.scalars().all())

    if not accounts:
        return {
            "type": "deposit_interest",
            "accounts_paid": 0,
            "total_interest_paid": 0.0,
        }

    bank = await _get_central_bank(db, lock=True)
    reserves = _to_decimal(bank.reserves)

    paid_count = 0
    total_paid = Decimal("0")

    for account in accounts:
        balance = _to_decimal(account.balance)
        interest = _round_money(balance * hourly_rate)

        if interest <= 0:
            continue

        # Only pay if reserves can cover it
        if reserves < interest:
            logger.warning(
                "Insufficient reserves to pay deposit interest (reserves: %.2f, interest: %.2f) — skipping",
                float(reserves),
                float(interest),
            )
            break  # If reserves are this low, stop paying interest

        account.balance = _round_money(balance + interest)
        reserves -= interest
        total_paid += interest
        paid_count += 1

        db.add(
            Transaction(
                type="deposit_interest",
                from_agent_id=None,  # from bank reserves
                to_agent_id=account.agent_id,
                amount=interest,
                metadata_json={
                    "account_balance_before": float(balance),
                    "rate": float(hourly_rate),
                    "tick_time": now.isoformat(),
                },
            )
        )

    bank.reserves = _round_money(reserves)
    await db.flush()

    logger.info(
        "Deposit interest: paid %.4f to %d accounts (%.6f hourly rate)",
        float(total_paid),
        paid_count,
        float(hourly_rate),
    )

    return {
        "type": "deposit_interest",
        "accounts_paid": paid_count,
        "total_interest_paid": float(total_paid),
        "hourly_rate": float(hourly_rate),
    }
