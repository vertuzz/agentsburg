"""
Credit scoring for Agent Economy banking.

Public functions:
  calculate_credit() — score agent creditworthiness and return loan terms
"""

from __future__ import annotations

import logging
from datetime import UTC
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.banking._helpers import (
    MAX_ACCOUNT_AGE_BONUS_DAYS,
    MAX_CREDIT_SCORE,
    MIN_CREDIT_SCORE,
    _get_active_policy,
    _round_money,
    _to_decimal,
)
from backend.models.banking import BankAccount
from backend.models.business import Business, Employment

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent

logger = logging.getLogger(__name__)


async def calculate_credit(
    db: AsyncSession,
    agent: Agent,
    clock: Clock,
    settings: Settings,
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
    result = await db.execute(select(BankAccount).where(BankAccount.agent_id == agent.id))
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

    # Loan-eligible net worth: illiquid assets count fully, but cash/bank
    # balances are capped at 50% to limit manipulation via cash transfers
    # between colluding agents (fractional reserve loan exploit).
    liquid_assets = wallet + bank_balance
    illiquid_assets = inventory_value + business_value
    loan_eligible_worth = illiquid_assets + min(liquid_assets, illiquid_assets + Decimal("200"))

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
            created = created.replace(tzinfo=UTC)
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
    # Use loan-eligible worth (illiquid fully + capped liquid) to prevent
    # manipulation via cash transfers between colluding agents.
    max_loan_multiplier = Decimal(str(settings.economy.max_loan_multiplier))
    base_max = loan_eligible_worth * max_loan_multiplier

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
