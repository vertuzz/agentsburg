"""
Economy snapshot generation.

Takes periodic snapshots of macro-level economy statistics including
GDP proxy, money supply, population, employment, wealth inequality (Gini),
active businesses, government type, and average bread price.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def calculate_gini(balances: list[float]) -> float:
    """
    Calculate the Gini coefficient from a list of balances.

    The Gini coefficient measures wealth inequality:
    - 0.0 = perfect equality (everyone has the same balance)
    - 1.0 = maximum inequality (one agent has everything)

    Uses the standard sorted-values formula:
        G = (2 * sum(i * x_i)) / (n * sum(x_i)) - (n + 1) / n
    where x_i are the sorted non-negative values and i is 1-indexed rank.

    Args:
        balances: List of agent balances (may include negatives, which are
                  clamped to 0 for the calculation).

    Returns:
        Gini coefficient in [0.0, 1.0], or 0.0 if fewer than 2 agents.
    """
    if len(balances) < 2:
        return 0.0

    # Clamp negatives to zero — wealth can't be negative for Gini purposes
    values = sorted(max(0.0, float(b)) for b in balances)
    n = len(values)
    total = sum(values)

    if total == 0.0:
        return 0.0

    weighted_sum = sum((i + 1) * v for i, v in enumerate(values))
    gini = (2.0 * weighted_sum) / (n * total) - (n + 1.0) / n
    return max(0.0, min(1.0, gini))


async def take_economy_snapshot(db: AsyncSession, now: datetime) -> None:
    """
    Take a snapshot of current macro-level economy statistics.

    Computes:
    - Total money supply (sum of all agent balances + bank reserves)
    - Population (active agents)
    - Employment rate (employed / total)
    - Gini coefficient of agent wealth distribution
    - Active business counts (player vs NPC)
    - Current government type
    - Average bread price (proxy for cost of living)
    """
    from backend.models.agent import Agent
    from backend.models.aggregate import EconomySnapshot
    from backend.models.banking import CentralBank
    from backend.models.business import Business, Employment
    from backend.models.government import GovernmentState
    from backend.models.marketplace import MarketTrade

    # Population: agents not currently bankrupt or fully dead
    pop_result = await db.execute(select(func.count()).select_from(Agent))
    population = pop_result.scalar_one() or 0

    # Money supply: sum all agent wallet balances + central bank reserves.
    # When an agent deposits, their wallet goes down and bank reserves go up,
    # so reserves already include deposit balances. Do NOT add BankAccount
    # balances separately — that would double-count deposits.
    # Formula: money_supply = sum(agent.balance) + central_bank.reserves
    agent_balance_result = await db.execute(select(func.sum(Agent.balance)))
    agent_balance_total = float(agent_balance_result.scalar_one() or 0)

    cb_result = await db.execute(select(CentralBank))
    cb = cb_result.scalars().first()
    bank_reserves = float(cb.reserves) if cb else 0.0

    money_supply = agent_balance_total + bank_reserves

    # Employment rate
    total_agents_result = await db.execute(select(func.count()).select_from(Agent))
    total_agents = total_agents_result.scalar_one() or 1

    employed_result = await db.execute(
        select(func.count(func.distinct(Employment.agent_id))).where(Employment.terminated_at.is_(None))
    )
    employed_count = employed_result.scalar_one() or 0
    employment_rate = employed_count / max(total_agents, 1)

    # Gini coefficient from all agent balances
    balance_result = await db.execute(select(Agent.balance))
    all_balances = [float(row[0]) for row in balance_result.fetchall()]
    gini = calculate_gini(all_balances) if all_balances else None

    # GDP proxy: recent transaction volume (last 6 hours)
    gdp_cutoff = now - timedelta(hours=6)
    from backend.models.transaction import Transaction

    gdp_result = await db.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.created_at >= gdp_cutoff,
            Transaction.type.in_(["marketplace", "storefront", "wage", "gathering"]),
        )
    )
    gdp = float(gdp_result.scalar_one() or 0)

    # Active businesses
    active_biz_result = await db.execute(select(func.count()).select_from(Business).where(Business.closed_at.is_(None)))
    active_businesses = active_biz_result.scalar_one() or 0

    npc_biz_result = await db.execute(
        select(func.count())
        .select_from(Business)
        .where(
            Business.closed_at.is_(None),
            Business.is_npc.is_(True),
        )
    )
    npc_businesses = npc_biz_result.scalar_one() or 0

    # Current government type
    gov_result = await db.execute(select(GovernmentState))
    gov = gov_result.scalars().first()
    government_type = gov.current_template_slug if gov else "unknown"

    # Average bread price: use recent trades
    bread_cutoff = now - timedelta(hours=6)
    bread_result = await db.execute(
        select(func.avg(MarketTrade.price)).where(
            MarketTrade.good_slug == "bread",
            MarketTrade.executed_at >= bread_cutoff,
        )
    )
    avg_bread_price_raw = bread_result.scalar_one()
    avg_bread_price = Decimal(str(avg_bread_price_raw)).quantize(Decimal("0.01")) if avg_bread_price_raw else None

    snapshot = EconomySnapshot(
        timestamp=now,
        gdp=Decimal(str(gdp)).quantize(Decimal("0.01")),
        money_supply=Decimal(str(money_supply)).quantize(Decimal("0.01")),
        population=population,
        employment_rate=employment_rate,
        gini_coefficient=gini,
        active_businesses=active_businesses,
        npc_businesses=npc_businesses,
        government_type=government_type,
        avg_bread_price=avg_bread_price,
    )
    db.add(snapshot)
    await db.flush()

    logger.info(
        "Economy snapshot: gdp=%.2f money=%.2f pop=%d emp=%.1f%% gini=%.3f",
        gdp,
        money_supply,
        population,
        employment_rate * 100,
        gini or 0,
    )
