"""
Shared helpers for banking sub-modules.

Internal utilities — not part of the public API.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.models.agent import Agent
from backend.models.banking import BankAccount, CentralBank

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.config import Settings
    from backend.models.banking import Loan

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


def _to_decimal(value) -> Decimal:
    """Safely convert a DB value (float, int, Decimal, str) to Decimal."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _round_money(value: Decimal) -> Decimal:
    """Round to 2 decimal places (currency precision)."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def lock_agent_for_update(db: AsyncSession, agent_id) -> Agent:
    """Lock and refresh an agent row before mutating any dependent state."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id).with_for_update().execution_options(populate_existing=True)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise ValueError(f"Agent not found during banking lock acquisition: {agent_id}")
    return agent


async def _get_or_create_account(db: AsyncSession, agent: Agent, *, lock: bool = False) -> BankAccount:
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
        raise RuntimeError("CentralBank singleton not found — run seed_central_bank() during bootstrap")
    return bank


async def lock_active_loans_for_agent(db: AsyncSession, agent_id) -> list[Loan]:
    """Lock an agent's active loans in stable primary-key order."""
    from backend.models.banking import Loan

    result = await db.execute(
        select(Loan)
        .where(
            Loan.agent_id == agent_id,
            Loan.status == "active",
        )
        .order_by(Loan.id)
        .with_for_update()
    )
    return list(result.scalars().all())


def _get_current_policy(settings: Settings) -> dict:
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


async def _get_active_policy(db: AsyncSession, settings: Settings) -> dict:
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
