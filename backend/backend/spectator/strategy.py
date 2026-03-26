"""
Strategy classification for spectator experience.

Analyses an agent's economic behaviour from existing data and assigns
a dominant strategy label plus a set of non-exclusive traits.

Results are cached in Redis for 1 hour to avoid repeated queries.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select

from backend.models.agent import Agent
from backend.models.banking import BankAccount, Loan
from backend.models.business import Business, Employment
from backend.models.government import TaxRecord, Violation
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

CACHE_TTL = 3600  # 1 hour


async def classify_agent(
    db: AsyncSession,
    agent_id: str,
    redis: aioredis.Redis,
    settings: object | None = None,
) -> dict:
    """Return ``{"strategy": "...", "traits": [...]}`` for the given agent.

    Checks Redis cache first; computes from DB on miss.
    *settings* is the app Settings object (needed for recipe-based vertical
    integration detection).
    """
    import uuid as _uuid

    uid = _uuid.UUID(agent_id) if isinstance(agent_id, str) else agent_id
    cache_key = f"spectator:strategy:{uid}"

    # --- cache hit? ---
    cached = await redis.get(cache_key)
    if cached is not None:
        try:
            return json.loads(cached)
        except json.JSONDecodeError, TypeError:
            pass

    # --- fetch data ---
    result = await db.execute(select(Agent).where(Agent.id == uid))
    agent = result.scalar_one_or_none()
    if agent is None:
        empty: dict = {"strategy": "unknown", "traits": []}
        return empty

    # Bank account
    bank_result = await db.execute(select(BankAccount).where(BankAccount.agent_id == uid))
    bank_account = bank_result.scalar_one_or_none()
    bank_balance = float(bank_account.balance) if bank_account else 0.0
    wallet_balance = float(agent.balance)
    total_wealth = wallet_balance + bank_balance

    # Open businesses owned by agent (non-NPC)
    biz_result = await db.execute(
        select(Business).where(
            and_(
                Business.owner_id == uid,
                Business.closed_at.is_(None),
                Business.is_npc.is_(False),
            )
        )
    )
    open_businesses = biz_result.scalars().all()

    # Active loans
    loan_result = await db.execute(
        select(func.count(Loan.id)).where(and_(Loan.agent_id == uid, Loan.status == "active"))
    )
    active_loan_count = loan_result.scalar() or 0

    # Violation count (from agent model directly)
    violation_count = agent.violation_count

    # Tax records with discrepancy
    tax_result = await db.execute(
        select(func.count(TaxRecord.id)).where(and_(TaxRecord.agent_id == uid, TaxRecord.discrepancy > 0))
    )
    tax_discrepancy_count = tax_result.scalar() or 0

    # Marketplace transaction count (as buyer/recipient)
    mkt_result = await db.execute(
        select(func.count(Transaction.id)).where(
            and_(
                Transaction.type == "marketplace",
                or_(
                    Transaction.to_agent_id == uid,
                    Transaction.from_agent_id == uid,
                ),
            )
        )
    )
    marketplace_tx_count = mkt_result.scalar() or 0

    # Other transaction count
    other_tx_result = await db.execute(
        select(func.count(Transaction.id)).where(
            and_(
                Transaction.type != "marketplace",
                or_(
                    Transaction.to_agent_id == uid,
                    Transaction.from_agent_id == uid,
                ),
            )
        )
    )
    other_tx_count = other_tx_result.scalar() or 0

    # Active employment (no open businesses)
    emp_result = await db.execute(
        select(func.count(Employment.id)).where(and_(Employment.agent_id == uid, Employment.terminated_at.is_(None)))
    )
    active_employment_count = emp_result.scalar() or 0

    # Employees working at agent's businesses
    has_employees = False
    if open_businesses:
        biz_ids = [b.id for b in open_businesses]
        emp_count_result = await db.execute(
            select(func.count(Employment.id)).where(
                and_(
                    Employment.business_id.in_(biz_ids),
                    Employment.terminated_at.is_(None),
                )
            )
        )
        has_employees = (emp_count_result.scalar() or 0) > 0

    # Violations with jail
    jail_result = await db.execute(
        select(func.count(Violation.id)).where(and_(Violation.agent_id == uid, Violation.jail_until.is_not(None)))
    )
    jail_count = jail_result.scalar() or 0

    # --- wealth ranking for tycoon ---
    # Count agents with higher total wealth
    all_agents_result = await db.execute(select(Agent))
    all_agents = all_agents_result.scalars().all()
    all_agent_ids = [a.id for a in all_agents]

    bank_accounts_result = await db.execute(select(BankAccount).where(BankAccount.agent_id.in_(all_agent_ids)))
    bank_map = {acc.agent_id: float(acc.balance) for acc in bank_accounts_result.scalars().all()}

    wealth_list = []
    for a in all_agents:
        w = float(a.balance) + bank_map.get(a.id, 0.0)
        wealth_list.append(w)
    wealth_list.sort(reverse=True)
    total_agents = len(wealth_list)
    top_10pct_threshold = wealth_list[max(0, int(total_agents * 0.1) - 1)] if total_agents > 0 else float("inf")

    # --- vertical integration detection ---
    is_vertical_integrator = False
    if len(open_businesses) >= 2 and settings is not None:
        recipes = getattr(settings, "recipes", [])
        # Build mapping: business_type -> set of output goods, set of input goods
        type_outputs: dict[str, set[str]] = {}
        type_inputs: dict[str, set[str]] = {}
        for recipe in recipes:
            btype = recipe.get("bonus_business_type", "")
            output = recipe.get("output_good", "")
            inputs = recipe.get("inputs", [])
            if btype:
                type_outputs.setdefault(btype, set()).add(output)
                for inp in inputs:
                    type_inputs.setdefault(btype, set()).add(inp.get("good", ""))

        owned_types = {b.type_slug for b in open_businesses}
        # Check if any owned business produces something another owned business consumes
        all_outputs: set[str] = set()
        all_inputs: set[str] = set()
        for t in owned_types:
            all_outputs |= type_outputs.get(t, set())
            all_inputs |= type_inputs.get(t, set())
        if all_outputs & all_inputs:
            is_vertical_integrator = True

    # --- traits ---
    traits: list[str] = []
    if open_businesses:
        traits.append("business_owner")
    if violation_count > 0:
        traits.append("tax_evader")
    if has_employees:
        traits.append("employer")
    if agent.bankruptcy_count > 0 and agent.is_active:
        traits.append("bankrupt_survivor")
    if jail_count > 0:
        traits.append("jailbird")
    if bank_balance > wallet_balance:
        traits.append("saver")
    if marketplace_tx_count > 0:
        traits.append("trader")

    # --- strategy classification (pick dominant) ---
    strategy = "unknown"

    if is_vertical_integrator:
        strategy = "vertical_integrator"
    elif bank_balance > 2 * wallet_balance and agent.bankruptcy_count == 0 and violation_count == 0:
        strategy = "conservative_saver"
    elif total_wealth >= top_10pct_threshold and open_businesses:
        strategy = "tycoon"
    elif violation_count > 0 or tax_discrepancy_count > 0:
        strategy = "tax_evader"
    elif len(open_businesses) >= 2 or active_loan_count > 0:
        strategy = "aggressive_expander"
    elif marketplace_tx_count > other_tx_count and marketplace_tx_count > 0:
        strategy = "market_trader"
    elif active_employment_count > 0 and not open_businesses:
        strategy = "wage_earner"

    result_data: dict = {"strategy": strategy, "traits": traits}

    # --- cache ---
    try:
        await redis.set(cache_key, json.dumps(result_data), ex=CACHE_TTL)
    except Exception:
        logger.warning("Failed to cache strategy for agent %s", agent_id)

    return result_data
