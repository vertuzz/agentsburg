"""
Banking service for Agent Economy.

Implements all banking operations:
  deposit()               — move money from agent wallet to bank account
  withdraw()              — move money from bank account to agent wallet
  calculate_credit()      — score agent creditworthiness → max loan & rate
  take_loan()             — disburse a new loan if credit & reserves allow
  process_loan_payments() — collect installments (slow tick / hourly)
  process_deposit_interest() — pay interest on deposits (slow tick / hourly)
  view_balance()          — return account snapshot

IMPORTANT: All money arithmetic uses Decimal for precision.
Float columns are read from the DB as Decimal via Numeric(20,2).

Money supply identity (maintained throughout):
  sum(agent.balance) + sum(bank_account.balance) + escrow + market_locks
    = initial_reserves + total_created_loans - total_repaid

The only sources of new money:
  - Loan disbursements (take_loan adds to agent.balance, reduces reserves)
The only sinks:
  - Loan repayments (reduce agent.balance, refill reserves)
  - Deposit interest (moves from reserves to bank_account — no net change in total)
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.banking import BankAccount, CentralBank, Loan
from backend.models.business import Business, Employment
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from backend.clock import Clock
    from backend.config import Settings

logger = logging.getLogger(__name__)

# Loan term: 24 hourly installments
LOAN_INSTALLMENTS = 24
# Installment interval: 1 hour in simulation time
INSTALLMENT_INTERVAL_HOURS = 1

# Credit score floor/ceiling
MIN_CREDIT_SCORE = 0
MAX_CREDIT_SCORE = 1000

# Account age bonus caps at this many days
MAX_ACCOUNT_AGE_BONUS_DAYS = 30


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_decimal(value) -> Decimal:
    """Safely convert a DB value (float, int, Decimal, str) to Decimal."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _round_money(value: Decimal) -> Decimal:
    """Round to 2 decimal places (currency precision)."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _get_or_create_account(
    db: AsyncSession, agent: Agent, *, lock: bool = False
) -> BankAccount:
    """
    Fetch the agent's bank account, creating it if it doesn't exist.

    Returns the BankAccount ORM object (potentially unflushed on first creation).
    When lock=True, acquires FOR UPDATE row lock to prevent concurrent mutations.
    """
    stmt = select(BankAccount).where(BankAccount.agent_id == agent.id)
    if lock:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()

    if account is None:
        account = BankAccount(agent_id=agent.id, balance=Decimal("0"))
        db.add(account)
        await db.flush()
        logger.info("Created bank account for agent %s", agent.name)

    return account


async def _get_central_bank(db: AsyncSession, *, lock: bool = False) -> CentralBank:
    """
    Fetch the CentralBank singleton.

    Raises RuntimeError if not found (bootstrap must run first).
    When lock=True, acquires FOR UPDATE row lock to prevent concurrent mutations.
    """
    stmt = select(CentralBank).where(CentralBank.id == 1)
    if lock:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    bank = result.scalar_one_or_none()
    if bank is None:
        raise RuntimeError(
            "CentralBank singleton not found — run seed_central_bank() during bootstrap"
        )
    return bank


def _get_current_policy(settings: "Settings") -> dict:
    """
    Extract current government policy from settings.

    Returns the active template's parameters, or defaults if no government
    template is configured yet (Phase 5 runs before Phase 6).
    """
    gov_config = settings.government
    templates = gov_config.get("templates", [])

    # Find the active template — in Phase 5, default to free_market
    # Phase 6 will override this with GovernmentState.current_template_slug
    active_slug = "free_market"

    # Try to find a configured active template
    for tmpl in templates:
        if tmpl.get("slug") == active_slug:
            return tmpl

    # Fallback: safe defaults (free_market values)
    return {
        "interest_rate_modifier": 1.0,
        "reserve_ratio": settings.economy.default_reserve_ratio,
    }


async def _get_active_policy(db: AsyncSession, settings: "Settings") -> dict:
    """
    Get active government policy, checking GovernmentState if available.

    Falls back gracefully if government tables don't exist yet.
    """
    try:
        from backend.models.government import GovernmentState  # Phase 6
        result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
        gov_state = result.scalar_one_or_none()

        if gov_state and gov_state.current_template_slug:
            templates = settings.government.get("templates", [])
            for tmpl in templates:
                if tmpl.get("slug") == gov_state.current_template_slug:
                    return tmpl
    except Exception:
        pass  # Phase 6 not yet in place

    return _get_current_policy(settings)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def deposit(
    db: AsyncSession,
    agent: Agent,
    amount: Decimal,
    clock: "Clock",
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
    agent_row = await db.execute(
        select(Agent).where(Agent.id == agent.id).with_for_update()
    )
    agent = agent_row.scalar_one()

    agent_balance = _to_decimal(agent.balance)
    if agent_balance < amount:
        raise ValueError(
            f"Insufficient wallet balance: have {float(agent_balance):.2f}, need {float(amount):.2f}"
        )

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
        agent.name, float(amount), float(account.balance), float(agent.balance),
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
    clock: "Clock",
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
    agent_row = await db.execute(
        select(Agent).where(Agent.id == agent.id).with_for_update()
    )
    agent = agent_row.scalar_one()

    account = await _get_or_create_account(db, agent, lock=True)
    account_balance = _to_decimal(account.balance)

    if account_balance < amount:
        raise ValueError(
            f"Insufficient account balance: have {float(account_balance):.2f}, need {float(amount):.2f}"
        )

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
        agent.name, float(amount), float(account.balance), float(agent.balance),
    )

    return {
        "action": "withdraw",
        "amount_withdrawn": float(amount),
        "wallet_balance": float(agent.balance),
        "account_balance": float(account.balance),
    }


async def calculate_credit(
    db: AsyncSession,
    agent: Agent,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Score an agent's creditworthiness and return loan terms.

    Credit score (0-1000) is based on:
      - Net worth: wallet + bank account balance + inventory value + business value
      - Employment: +50 points if currently employed
      - Account age: up to +100 points for accounts > 30 days old
      - Bankruptcy history: -200 points per bankruptcy
      - Violation history: -20 points per violation

    Max loan amount:
      base_max = net_worth * max_loan_multiplier (from settings)
      Each bankruptcy halves the max loan.
      Minimum: 0 (can't borrow if bankrupt with no assets).

    Interest rate:
      base = settings.economy.base_loan_interest_rate
      + (bankruptcy_count * 0.02)
      - account_age_bonus (up to 0.02)
      * government.interest_rate_modifier
      Minimum rate: 0.01 (1%)

    Args:
        db:       Active async session.
        agent:    Agent to score.
        clock:    For age calculation.
        settings: Application settings.

    Returns:
        Dict with credit_score, max_loan_amount, interest_rate.
    """
    now = clock.now()
    policy = await _get_active_policy(db, settings)

    # --- Compute net worth ---
    wallet = _to_decimal(agent.balance)

    # Bank account balance
    result = await db.execute(
        select(BankAccount).where(BankAccount.agent_id == agent.id)
    )
    account = result.scalar_one_or_none()
    bank_balance = _to_decimal(account.balance) if account else Decimal("0")

    # Inventory value (use goods base_value)
    goods_config = {g["slug"]: g for g in settings.goods}
    from backend.models.inventory import InventoryItem
    inv_result = await db.execute(
        select(InventoryItem).where(
            InventoryItem.owner_type == "agent",
            InventoryItem.owner_id == agent.id,
            InventoryItem.quantity > 0,
        )
    )
    inventory_items = inv_result.scalars().all()
    inventory_value = Decimal("0")
    for item in inventory_items:
        good_data = goods_config.get(item.good_slug)
        if good_data:
            inventory_value += _to_decimal(good_data.get("base_value", 0)) * item.quantity

    # Business value (rough estimate: business_registration_cost per open business)
    biz_result = await db.execute(
        select(Business).where(
            Business.owner_id == agent.id,
            Business.closed_at.is_(None),
        )
    )
    owned_businesses = biz_result.scalars().all()
    business_value = _to_decimal(settings.economy.business_registration_cost) * len(owned_businesses)

    net_worth = wallet + bank_balance + inventory_value + business_value

    # --- Employment status ---
    emp_result = await db.execute(
        select(Employment).where(
            Employment.agent_id == agent.id,
            Employment.terminated_at.is_(None),
        )
    )
    employed = emp_result.scalar_one_or_none() is not None

    # --- Account age ---
    account_age_days = 0.0
    if agent.created_at is not None:
        created = agent.created_at
        if created.tzinfo is None:
            from datetime import timezone
            created = created.replace(tzinfo=timezone.utc)
        age_seconds = (now - created).total_seconds()
        account_age_days = max(0.0, age_seconds / 86400)

    # --- Credit score calculation ---
    score = Decimal("500")  # Base score

    # Net worth contribution: up to +200 points for large net worth
    # Scale: 0 net worth = 0, 10000 net worth = +100, 100000 = +200 (log-ish)
    if net_worth > 0:
        worth_bonus = min(Decimal("200"), net_worth / Decimal("500"))
        score += worth_bonus

    # Employment: +50
    if employed:
        score += Decimal("50")

    # Account age: up to +100 (scales over 30 days)
    age_bonus_days = min(account_age_days, MAX_ACCOUNT_AGE_BONUS_DAYS)
    age_score = Decimal(str(age_bonus_days / MAX_ACCOUNT_AGE_BONUS_DAYS)) * Decimal("100")
    score += age_score

    # Bankruptcy penalty: -200 each (very harsh)
    score -= Decimal(str(agent.bankruptcy_count)) * Decimal("200")

    # Violation penalty: -20 each
    score -= Decimal(str(agent.violation_count)) * Decimal("20")

    credit_score = int(max(MIN_CREDIT_SCORE, min(MAX_CREDIT_SCORE, float(score))))

    # --- Max loan amount ---
    max_loan_multiplier = Decimal(str(settings.economy.max_loan_multiplier))
    base_max = net_worth * max_loan_multiplier

    # Each bankruptcy halves the max loan
    for _ in range(agent.bankruptcy_count):
        base_max = base_max / Decimal("2")

    # Minimum 0 — no loans for agents with nothing
    max_loan_amount = _round_money(max(Decimal("0"), base_max))

    # --- Interest rate ---
    base_rate = Decimal(str(settings.economy.base_loan_interest_rate))
    bankruptcy_penalty = Decimal(str(agent.bankruptcy_count)) * Decimal("0.02")

    # Account age bonus: up to 0.02 reduction (capped)
    age_bonus_rate = min(
        Decimal("0.02"),
        Decimal(str(account_age_days / MAX_ACCOUNT_AGE_BONUS_DAYS)) * Decimal("0.02"),
    )

    raw_rate = base_rate + bankruptcy_penalty - age_bonus_rate

    # Apply government modifier
    interest_rate_modifier = Decimal(str(policy.get("interest_rate_modifier", 1.0)))
    adjusted_rate = raw_rate * interest_rate_modifier

    # Floor at 1%
    interest_rate = float(max(Decimal("0.01"), adjusted_rate))

    return {
        "credit_score": credit_score,
        "max_loan_amount": float(max_loan_amount),
        "interest_rate": round(interest_rate, 4),
        "net_worth": float(net_worth),
        "components": {
            "wallet": float(wallet),
            "bank_balance": float(bank_balance),
            "inventory_value": float(inventory_value),
            "business_value": float(business_value),
            "employed": employed,
            "bankruptcy_count": agent.bankruptcy_count,
            "violation_count": agent.violation_count,
            "account_age_days": round(account_age_days, 1),
        },
    }


async def take_loan(
    db: AsyncSession,
    agent: Agent,
    amount: Decimal,
    clock: "Clock",
    settings: "Settings",
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
    amount = _to_decimal(amount)
    if amount <= 0:
        raise ValueError("Loan amount must be positive")
    if amount < Decimal("1"):
        raise ValueError("Minimum loan amount is 1")

    # Lock agent row to prevent concurrent loan/balance manipulation
    agent_row = await db.execute(
        select(Agent).where(Agent.id == agent.id).with_for_update()
    )
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
            "Your credit score does not qualify for any loan. "
            "Build net worth, avoid bankruptcies, and try again."
        )

    # --- Check one-loan-at-a-time (locked to prevent double-loan race) ---
    existing_result = await db.execute(
        select(Loan).where(
            Loan.agent_id == agent.id,
            Loan.status == "active",
        ).with_for_update()
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
        agent.name, float(amount), interest_rate, float(installment_amount),
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


async def process_loan_payments(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
) -> dict:
    """
    Collect loan installments from all agents with active, due loans.

    Called every hour (slow tick). Finds all active loans where
    next_payment_at <= clock.now() and attempts to deduct the installment.

    If the agent cannot pay (insufficient wallet balance):
      - The loan is marked "defaulted"
      - The agent is flagged for bankruptcy processing (balance set to threshold)
      - bank.total_loaned reduced by the remaining balance (debt is written off)

    If the agent can pay:
      - agent.balance -= installment_amount
      - loan.remaining_balance -= installment_amount
      - bank.reserves += installment_amount  (money flows back into bank)
      - loan.total_loaned -= installment_amount (partial payoff)
      - If installments_remaining reaches 0: status = "paid_off"

    Transaction type: "loan_payment"

    Returns:
        Summary dict with payment counts, default counts, amounts collected.
    """
    now = clock.now()

    # Load all active, due loans with their agents in one pass
    result = await db.execute(
        select(Loan).where(
            Loan.status == "active",
            Loan.next_payment_at <= now,
        )
    )
    due_loans = list(result.scalars().all())

    if not due_loans:
        return {
            "type": "loan_payments",
            "processed": 0,
            "paid": 0,
            "defaulted": 0,
            "total_collected": 0.0,
        }

    # Load agents in batch
    agent_ids = {loan.agent_id for loan in due_loans}
    agents_result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
    agents_by_id = {a.id: a for a in agents_result.scalars().all()}

    bank = await _get_central_bank(db)
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

        if loan.installments_remaining == 1:
            # Final installment: pay exact remaining balance to avoid rounding drift
            installment = _to_decimal(loan.remaining_balance)
        else:
            installment = _to_decimal(loan.installment_amount)
        agent_balance = _to_decimal(agent.balance)

        if agent_balance >= installment:
            # --- Happy path: agent pays the installment ---
            agent.balance = _round_money(agent_balance - installment)

            # Update loan state
            remaining = _to_decimal(loan.remaining_balance) - installment
            loan.remaining_balance = _round_money(max(Decimal("0"), remaining))
            loan.installments_remaining = max(0, loan.installments_remaining - 1)

            # Money flows back into reserves; reduce outstanding loans
            reserves += installment
            total_loaned -= installment
            total_collected += installment

            if loan.installments_remaining == 0:
                loan.status = "paid_off"
                logger.info(
                    "Loan %s fully paid off by agent %s", loan.id, agent.name
                )
            else:
                loan.next_payment_at = now + timedelta(hours=INSTALLMENT_INTERVAL_HOURS)

            paid_count += 1

            db.add(Transaction(
                type="loan_payment",
                from_agent_id=agent.id,
                to_agent_id=None,  # to bank
                amount=installment,
                metadata_json={
                    "loan_id": str(loan.id),
                    "installments_remaining": loan.installments_remaining,
                    "tick_time": now.isoformat(),
                },
            ))

        else:
            # --- Default: agent cannot pay ---
            logger.warning(
                "Agent %s defaulted on loan %s (balance: %.2f, installment: %.2f)",
                agent.name, loan.id, float(agent_balance), float(installment),
            )

            # Write off the remaining loan balance from total_loaned
            remaining_principal = _to_decimal(loan.remaining_balance)
            total_loaned -= remaining_principal  # bank absorbs the loss

            loan.status = "defaulted"
            loan.remaining_balance = Decimal("0")

            # Push agent below bankruptcy threshold to trigger bankruptcy
            agent.balance = _round_money(bankruptcy_threshold - Decimal("1"))

            defaulted_count += 1

            db.add(Transaction(
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
            ))

    # Write back bank totals
    bank.reserves = _round_money(reserves)
    bank.total_loaned = _round_money(max(Decimal("0"), total_loaned))

    await db.flush()

    logger.info(
        "Loan payments: %d processed (%d paid, %d defaulted), collected %.2f",
        len(due_loans), paid_count, defaulted_count, float(total_collected),
    )

    return {
        "type": "loan_payments",
        "processed": len(due_loans),
        "paid": paid_count,
        "defaulted": defaulted_count,
        "total_collected": float(total_collected),
    }


async def process_deposit_interest(
    db: AsyncSession,
    clock: "Clock",
    settings: "Settings",
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
    result = await db.execute(
        select(BankAccount).where(BankAccount.balance >= min_balance)
    )
    accounts = list(result.scalars().all())

    if not accounts:
        return {
            "type": "deposit_interest",
            "accounts_paid": 0,
            "total_interest_paid": 0.0,
        }

    bank = await _get_central_bank(db)
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
                float(reserves), float(interest),
            )
            break  # If reserves are this low, stop paying interest

        account.balance = _round_money(balance + interest)
        reserves -= interest
        total_paid += interest
        paid_count += 1

        db.add(Transaction(
            type="deposit_interest",
            from_agent_id=None,  # from bank reserves
            to_agent_id=account.agent_id,
            amount=interest,
            metadata_json={
                "account_balance_before": float(balance),
                "rate": float(hourly_rate),
                "tick_time": now.isoformat(),
            },
        ))

    bank.reserves = _round_money(reserves)
    await db.flush()

    logger.info(
        "Deposit interest: paid %.4f to %d accounts (%.6f hourly rate)",
        float(total_paid), paid_count, float(hourly_rate),
    )

    return {
        "type": "deposit_interest",
        "accounts_paid": paid_count,
        "total_interest_paid": float(total_paid),
        "hourly_rate": float(hourly_rate),
    }


async def view_balance(
    db: AsyncSession,
    agent: Agent,
    clock: "Clock",
    settings: "Settings",
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
        loan_data.append({
            "loan_id": str(loan.id),
            "principal": float(loan.principal),
            "remaining_balance": float(loan.remaining_balance),
            "interest_rate": loan.interest_rate,
            "installment_amount": float(loan.installment_amount),
            "installments_remaining": loan.installments_remaining,
            "next_payment_at": loan.next_payment_at.isoformat(),
            "status": loan.status,
        })

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


async def default_agent_loans(
    db: AsyncSession,
    agent: Agent,
    clock: "Clock",
) -> dict:
    """
    Default all active loans for a bankrupt agent.

    Called from bankruptcy.py when an agent is being processed.
    Writes off remaining balances from total_loaned (bank absorbs the loss).

    Args:
        db:    Active async session.
        agent: The bankrupt agent.
        clock: For timestamps.

    Returns:
        Dict with count of loans defaulted and total written off.
    """
    now = clock.now()

    result = await db.execute(
        select(Loan).where(
            Loan.agent_id == agent.id,
            Loan.status == "active",
        )
    )
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

        db.add(Transaction(
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
        ))

    bank.total_loaned = _round_money(max(Decimal("0"), total_loaned))
    await db.flush()

    logger.info(
        "Defaulted %d loans for bankrupt agent %s, wrote off %.2f",
        len(active_loans), agent.name, float(total_written_off),
    )

    return {
        "loans_defaulted": len(active_loans),
        "total_written_off": float(total_written_off),
    }


async def close_bank_account_for_bankruptcy(
    db: AsyncSession,
    agent: Agent,
    clock: "Clock",
) -> dict:
    """
    Liquidate a bank account during bankruptcy, seizing deposits to pay loans first.

    Order:
      1. Seize bank deposits
      2. Use deposits to pay down any active loans
      3. If deposits > total loan balance: pay off loans, remainder goes to agent wallet
      4. If deposits <= total loan balance: apply all deposits to loans, write off the rest

    This prevents the exploit: take loan -> deposit everything -> default -> recover deposits.

    Args:
        db:    Active async session.
        agent: The bankrupt agent.
        clock: For timestamps.

    Returns:
        Dict with amount recovered, amount applied to loans, and amount written off.
    """
    now = clock.now()

    result = await db.execute(
        select(BankAccount).where(BankAccount.agent_id == agent.id)
    )
    account = result.scalar_one_or_none()
    deposit_balance = _to_decimal(account.balance) if account else Decimal("0")

    # Fetch active loans
    loans_result = await db.execute(
        select(Loan).where(
            Loan.agent_id == agent.id,
            Loan.status == "active",
        )
    )
    active_loans = list(loans_result.scalars().all())

    bank = await _get_central_bank(db)
    total_loaned = _to_decimal(bank.total_loaned)

    # Calculate total loan debt
    total_loan_debt = Decimal("0")
    for loan in active_loans:
        total_loan_debt += _to_decimal(loan.remaining_balance)

    # Apply deposits toward loans first
    applied_to_loans = Decimal("0")
    written_off = Decimal("0")
    remaining_deposits = deposit_balance

    for loan in active_loans:
        loan_remaining = _to_decimal(loan.remaining_balance)
        if remaining_deposits >= loan_remaining:
            # Deposits can cover this loan fully
            applied_to_loans += loan_remaining
            remaining_deposits -= loan_remaining
            total_loaned -= loan_remaining
            loan.status = "defaulted"
            loan.remaining_balance = Decimal("0")

            db.add(Transaction(
                type="loan_payment",
                from_agent_id=agent.id,
                to_agent_id=None,
                amount=loan_remaining,
                metadata_json={
                    "loan_id": str(loan.id),
                    "status": "bankruptcy_seized_deposits",
                    "tick_time": now.isoformat(),
                },
            ))
        else:
            # Deposits only partially cover this loan
            if remaining_deposits > 0:
                applied_to_loans += remaining_deposits
                loan_remaining -= remaining_deposits
                total_loaned -= remaining_deposits

                db.add(Transaction(
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
                ))
                remaining_deposits = Decimal("0")

            # Write off what remains of this loan
            written_off += loan_remaining
            total_loaned -= loan_remaining
            loan.status = "defaulted"
            loan.remaining_balance = Decimal("0")

            db.add(Transaction(
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
            ))

    # Zero out the bank account
    if account is not None:
        account.balance = Decimal("0")

    # Deposits that went to loan repayment flow back into reserves
    # (they were already in reserves as deposits, and now repay loans)
    # Net effect on reserves: deposits stay in reserves (account closed),
    # but applied_to_loans reduces total_loaned (debt repaid from seized deposits).
    # remaining_deposits go to agent wallet and leave reserves.
    if remaining_deposits > 0:
        agent.balance = _round_money(_to_decimal(agent.balance) + remaining_deposits)
        bank.reserves = _round_money(_to_decimal(bank.reserves) - remaining_deposits)

        db.add(Transaction(
            type="withdrawal",
            from_agent_id=None,
            to_agent_id=agent.id,
            amount=remaining_deposits,
            metadata_json={
                "reason": "bankruptcy_account_remainder",
                "tick_time": now.isoformat(),
            },
        ))

    bank.total_loaned = _round_money(max(Decimal("0"), total_loaned))

    await db.flush()

    logger.info(
        "Bankruptcy account closure for agent %s: deposits=%.2f, applied_to_loans=%.2f, "
        "written_off=%.2f, returned_to_wallet=%.2f",
        agent.name, float(deposit_balance), float(applied_to_loans),
        float(written_off), float(remaining_deposits),
    )

    return {
        "account_balance_recovered": float(remaining_deposits),
        "deposits_applied_to_loans": float(applied_to_loans),
        "loan_debt_written_off": float(written_off),
    }
