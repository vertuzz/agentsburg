"""
Achievement badge computation for spectator experience.

Each badge is a simple predicate evaluated against existing data.
Results are cached in Redis for 1 hour.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select

from backend.models.agent import Agent
from backend.models.banking import BankAccount
from backend.models.business import Business, Employment
from backend.models.government import Violation
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    import uuid as _uuid

    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock

logger = logging.getLogger(__name__)

CACHE_TTL = 3600  # 1 hour

# Badge definitions: slug, name, description
BADGE_DEFS: list[dict[str, str]] = [
    {"slug": "first_business", "name": "Entrepreneur", "description": "Owns or has owned at least one business"},
    {"slug": "tycoon", "name": "Tycoon", "description": "Total wealth in top 5 agents"},
    {"slug": "tax_evader", "name": "Tax Evader", "description": "Caught evading taxes"},
    {"slug": "honest_citizen", "name": "Honest Citizen", "description": "Zero violations for over 24 hours"},
    {
        "slug": "comeback_kid",
        "name": "Comeback Kid",
        "description": "Went bankrupt but bounced back with positive balance",
    },
    {"slug": "employer", "name": "Job Creator", "description": "Has active employees"},
    {"slug": "jailbird", "name": "Jailbird", "description": "Has been jailed"},
    {"slug": "market_maker", "name": "Market Maker", "description": "Completed 10+ marketplace transactions"},
    {"slug": "survivor", "name": "Survivor", "description": "Among the oldest agents and still has positive balance"},
]


async def compute_badges(
    db: AsyncSession,
    agent_id: str | _uuid.UUID,
    redis: aioredis.Redis,
    clock: Clock,
) -> list[dict]:
    """Return list of earned badge dicts for the given agent."""
    import uuid as _uuid_mod

    uid = _uuid_mod.UUID(agent_id) if isinstance(agent_id, str) else agent_id
    cache_key = f"spectator:badges:{uid}"

    # --- cache hit? ---
    cached = await redis.get(cache_key)
    if cached is not None:
        try:
            return json.loads(cached)
        except json.JSONDecodeError, TypeError:
            pass

    # --- fetch agent ---
    result = await db.execute(select(Agent).where(Agent.id == uid))
    agent = result.scalar_one_or_none()
    if agent is None:
        return []

    now = clock.now()
    earned: list[dict] = []

    # first_business — owns or has owned at least one business (including closed)
    biz_count_result = await db.execute(
        select(func.count(Business.id)).where(and_(Business.owner_id == uid, Business.is_npc.is_(False)))
    )
    if (biz_count_result.scalar() or 0) > 0:
        earned.append(_badge("first_business"))

    # tycoon — top 5 by total wealth
    wallet = float(agent.balance)
    bank_result = await db.execute(select(BankAccount).where(BankAccount.agent_id == uid))
    bank_acc = bank_result.scalar_one_or_none()
    bank_bal = float(bank_acc.balance) if bank_acc else 0.0
    my_wealth = wallet + bank_bal

    # Get all agents' wealth to rank
    all_agents_result = await db.execute(select(Agent))
    all_agents = all_agents_result.scalars().all()
    all_ids = [a.id for a in all_agents]
    bank_accounts_result = await db.execute(select(BankAccount).where(BankAccount.agent_id.in_(all_ids)))
    bank_map = {acc.agent_id: float(acc.balance) for acc in bank_accounts_result.scalars().all()}

    wealth_list = sorted(
        (float(a.balance) + bank_map.get(a.id, 0.0) for a in all_agents),
        reverse=True,
    )
    top5_threshold = wealth_list[4] if len(wealth_list) >= 5 else wealth_list[-1] if wealth_list else float("inf")
    if my_wealth >= top5_threshold:
        earned.append(_badge("tycoon"))

    # tax_evader — has any Violation with type='tax_evasion'
    tax_viol_result = await db.execute(
        select(func.count(Violation.id)).where(and_(Violation.agent_id == uid, Violation.type == "tax_evasion"))
    )
    if (tax_viol_result.scalar() or 0) > 0:
        earned.append(_badge("tax_evader"))

    # honest_citizen — 0 violations AND agent older than 24h
    if agent.violation_count == 0 and agent.created_at is not None:
        age = now - agent.created_at
        if age > timedelta(hours=24):
            earned.append(_badge("honest_citizen"))

    # comeback_kid — bankruptcy_count > 0 AND is_active AND balance > 0
    if agent.bankruptcy_count > 0 and agent.is_active and float(agent.balance) > 0:
        earned.append(_badge("comeback_kid"))

    # employer — has active employees at owned businesses
    open_biz_result = await db.execute(
        select(Business.id).where(
            and_(
                Business.owner_id == uid,
                Business.closed_at.is_(None),
                Business.is_npc.is_(False),
            )
        )
    )
    open_biz_ids = [row[0] for row in open_biz_result.all()]
    if open_biz_ids:
        emp_count_result = await db.execute(
            select(func.count(Employment.id)).where(
                and_(
                    Employment.business_id.in_(open_biz_ids),
                    Employment.terminated_at.is_(None),
                )
            )
        )
        if (emp_count_result.scalar() or 0) > 0:
            earned.append(_badge("employer"))

    # jailbird — any Violation with jail_until not None
    jail_result = await db.execute(
        select(func.count(Violation.id)).where(and_(Violation.agent_id == uid, Violation.jail_until.is_not(None)))
    )
    if (jail_result.scalar() or 0) > 0:
        earned.append(_badge("jailbird"))

    # market_maker — 10+ marketplace transactions
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
    if (mkt_result.scalar() or 0) >= 10:
        earned.append(_badge("market_maker"))

    # survivor — oldest 25% of agents AND balance > 0
    if float(agent.balance) > 0 and all_agents:
        created_dates = sorted(a.created_at for a in all_agents if a.created_at is not None)
        if created_dates:
            cutoff_idx = max(0, len(created_dates) // 4 - 1)
            oldest_cutoff = created_dates[cutoff_idx]
            if agent.created_at is not None and agent.created_at <= oldest_cutoff:
                earned.append(_badge("survivor"))

    # --- cache ---
    try:
        await redis.set(cache_key, json.dumps(earned), ex=CACHE_TTL)
    except Exception:
        logger.warning("Failed to cache badges for agent %s", agent_id)

    return earned


def _badge(slug: str) -> dict:
    """Look up full badge dict by slug."""
    for b in BADGE_DEFS:
        if b["slug"] == slug:
            return dict(b)
    return {"slug": slug, "name": slug, "description": ""}
