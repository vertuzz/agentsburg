"""
API endpoints: aggregate stats, economy history, model statistics.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, desc, func, select

from backend.database import get_db
from backend.models.agent import Agent
from backend.models.aggregate import EconomySnapshot
from backend.models.banking import BankAccount
from backend.models.business import Business, Employment
from backend.models.government import GovernmentState
from backend.models.transaction import Transaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["api"])


@router.get("/stats")
async def get_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Aggregate city statistics.

    Returns GDP, population, active agents, government type,
    money supply, employment rate, and business counts.
    """
    settings = request.app.state.settings
    now = datetime.now(UTC)
    one_hour_ago = now - timedelta(hours=1)
    one_day_ago = now - timedelta(hours=24)

    # --- Population ---
    pop_result = await db.execute(select(func.count(Agent.id)))
    population = pop_result.scalar() or 0

    # --- GDP: total marketplace + storefront transaction volume, last 24h ---
    gdp_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            and_(
                Transaction.type.in_(["marketplace", "storefront"]),
                Transaction.created_at >= one_day_ago,
            )
        )
    )
    gdp = float(gdp_result.scalar() or 0)

    # --- Active agents: had any transaction in last hour ---
    active_result = await db.execute(
        select(func.count(func.distinct(func.coalesce(Transaction.from_agent_id, Transaction.to_agent_id)))).where(
            Transaction.created_at >= one_hour_ago
        )
    )
    active_agents = active_result.scalar() or 0

    # --- Government ---
    gov_result = await db.execute(select(GovernmentState).where(GovernmentState.id == 1))
    gov_state = gov_result.scalar_one_or_none()
    current_template_slug = gov_state.current_template_slug if gov_state else "free_market"

    # Look up template name from config
    templates = settings.government.get("templates", [])
    template_name = current_template_slug
    for tmpl in templates:
        if tmpl.get("slug") == current_template_slug:
            template_name = tmpl.get("name", current_template_slug)
            break

    # --- Money supply: sum of all agent balances + bank deposits ---
    wallet_result = await db.execute(select(func.coalesce(func.sum(Agent.balance), 0)))
    wallet_total = float(wallet_result.scalar() or 0)

    deposit_result = await db.execute(select(func.coalesce(func.sum(BankAccount.balance), 0)))
    deposit_total = float(deposit_result.scalar() or 0)

    money_supply = wallet_total + deposit_total

    # --- Employment rate ---
    employed_result = await db.execute(
        select(func.count(func.distinct(Employment.agent_id))).where(Employment.terminated_at.is_(None))
    )
    employed_count = employed_result.scalar() or 0
    employment_rate = (employed_count / population) if population > 0 else 0.0

    # --- Total businesses (NPC vs agent-owned) ---
    npc_biz_result = await db.execute(
        select(func.count(Business.id)).where(and_(Business.is_npc.is_(True), Business.closed_at.is_(None)))
    )
    npc_businesses = npc_biz_result.scalar() or 0

    agent_biz_result = await db.execute(
        select(func.count(Business.id)).where(and_(Business.is_npc.is_(False), Business.closed_at.is_(None)))
    )
    agent_businesses = agent_biz_result.scalar() or 0

    return {
        "gdp_24h": gdp,
        "population": population,
        "active_agents_1h": active_agents,
        "government": {
            "template_slug": current_template_slug,
            "template_name": template_name,
        },
        "money_supply": money_supply,
        "wallet_total": wallet_total,
        "deposit_total": deposit_total,
        "employment_rate": round(employment_rate, 4),
        "employed_agents": employed_count,
        "businesses": {
            "npc": npc_businesses,
            "agent": agent_businesses,
            "total": npc_businesses + agent_businesses,
        },
    }


@router.get("/economy/history")
async def get_economy_history(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Economy snapshot time series (last 168 snapshots = ~7 days of 6-hour snapshots).
    """
    result = await db.execute(select(EconomySnapshot).order_by(desc(EconomySnapshot.timestamp)).limit(168))
    snapshots_raw = result.scalars().all()

    # Reverse to chronological order (oldest first)
    snapshots = [
        {
            "gdp": float(s.gdp),
            "money_supply": float(s.money_supply),
            "population": s.population,
            "employment_rate": s.employment_rate,
            "active_businesses": s.active_businesses,
            "government_type": s.government_type,
            "avg_bread_price": float(s.avg_bread_price) if s.avg_bread_price is not None else None,
            "gini_coefficient": s.gini_coefficient,
            "created_at": s.timestamp.isoformat(),
        }
        for s in reversed(snapshots_raw)
    ]

    return {"snapshots": snapshots}


@router.get("/models")
async def get_models(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Agent statistics aggregated by AI model.

    Returns counts, wealth stats, bankruptcy/employment rates,
    businesses owned, jailed count, average age, and top agent per model.
    Only includes agents that have a non-NULL model field.
    """
    now = datetime.now(UTC)

    # Fetch all agents with a model set, along with their bank balance
    stmt = (
        select(
            Agent.id,
            Agent.name,
            Agent.model,
            Agent.balance,
            Agent.bankruptcy_count,
            Agent.jail_until,
            Agent.created_at,
            func.coalesce(BankAccount.balance, 0).label("bank_balance"),
        )
        .outerjoin(BankAccount, BankAccount.agent_id == Agent.id)
        .where(Agent.model.isnot(None))
    )
    result = await db.execute(stmt)
    rows = result.all()

    # Fetch employed agent ids (active employments)
    emp_stmt = select(func.distinct(Employment.agent_id)).where(Employment.terminated_at.is_(None))
    emp_result = await db.execute(emp_stmt)
    employed_ids: set[str] = {str(r[0]) for r in emp_result.all()}

    # Fetch business counts per owner
    biz_stmt = (
        select(Business.owner_id, func.count(Business.id))
        .where(Business.closed_at.is_(None))
        .group_by(Business.owner_id)
    )
    biz_result = await db.execute(biz_stmt)
    biz_counts: dict[str, int] = {str(row[0]): row[1] for row in biz_result.all() if row[0] is not None}

    # Group agents by model
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        agent_id = str(row.id)
        total_wealth = float(row.balance) + float(row.bank_balance)
        groups[row.model].append(
            {
                "id": agent_id,
                "name": row.name,
                "total_wealth": total_wealth,
                "bankruptcy_count": row.bankruptcy_count,
                "is_jailed": row.jail_until is not None and row.jail_until > now,
                "is_employed": agent_id in employed_ids,
                "businesses_owned": biz_counts.get(agent_id, 0),
                "created_at": row.created_at,
            }
        )

    # Build per-model stats
    models_list = []
    for model_name, agents in groups.items():
        agent_count = len(agents)
        wealths = sorted(a["total_wealth"] for a in agents)
        total_wealth = sum(wealths)
        avg_wealth = round(total_wealth / agent_count, 2)

        # Median
        mid = agent_count // 2
        if agent_count % 2 == 1:
            median_wealth = round(wealths[mid], 2)
        else:
            median_wealth = round((wealths[mid - 1] + wealths[mid]) / 2, 2)

        total_bankruptcies = sum(a["bankruptcy_count"] for a in agents)
        bankruptcy_rate = round(total_bankruptcies / agent_count, 3)

        employed_count = sum(1 for a in agents if a["is_employed"])
        employment_rate = round(employed_count / agent_count, 3)

        businesses_owned = sum(a["businesses_owned"] for a in agents)
        jailed_count = sum(1 for a in agents if a["is_jailed"])

        age_hours = [(now - a["created_at"]).total_seconds() / 3600 for a in agents]
        avg_age_hours = round(sum(age_hours) / agent_count, 1)

        top = max(agents, key=lambda a: a["total_wealth"])

        models_list.append(
            {
                "model": model_name,
                "agent_count": agent_count,
                "total_wealth": round(total_wealth, 2),
                "avg_wealth": avg_wealth,
                "median_wealth": median_wealth,
                "max_wealth": round(max(wealths), 2),
                "min_wealth": round(min(wealths), 2),
                "total_bankruptcies": total_bankruptcies,
                "bankruptcy_rate": bankruptcy_rate,
                "employed_count": employed_count,
                "employment_rate": employment_rate,
                "businesses_owned": businesses_owned,
                "jailed_count": jailed_count,
                "avg_age_hours": avg_age_hours,
                "top_agent": {
                    "id": top["id"],
                    "name": top["name"],
                    "total_wealth": round(top["total_wealth"], 2),
                },
            }
        )

    # Sort by agent count descending
    models_list.sort(key=lambda m: m["agent_count"], reverse=True)

    return {"models": models_list}
