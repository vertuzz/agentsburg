"""
API endpoints: agent list and agent profile.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.agent import Agent
from backend.models.banking import BankAccount
from backend.models.business import Business, Employment
from backend.models.government import Violation
from backend.models.inventory import InventoryItem
from backend.models.transaction import Transaction
from backend.models.zone import Zone

router = APIRouter(tags=["api"])


@router.get("/agents")
async def get_agents_list(
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=100),
    exclude_npc: bool = Query(False, description="Exclude NPC agents"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Public list of all agents with limited info, ordered by total wealth DESC.
    """
    # Count total agents
    count_query = select(func.count(Agent.id))
    if exclude_npc:
        count_query = count_query.where(Agent.is_npc == False)  # noqa: E712
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size

    # Fetch all agents (limited page)
    agents_query = select(Agent)
    if exclude_npc:
        agents_query = agents_query.where(Agent.is_npc == False)  # noqa: E712
    await db.execute(
        agents_query.order_by(desc(Agent.balance))
        .limit(page_size * 3)  # over-fetch to sort by total wealth
        .offset(0)
    )
    # We need total wealth = wallet + bank, so fetch all agents then sort
    # For correctness with pagination, fetch all and sort in Python
    all_agents_result = await db.execute(agents_query)
    all_agents = all_agents_result.scalars().all()

    # Get bank accounts for all agents
    if all_agents:
        agent_ids = [a.id for a in all_agents]
        accounts_result = await db.execute(select(BankAccount).where(BankAccount.agent_id.in_(agent_ids)))
        accounts = {acc.agent_id: float(acc.balance) for acc in accounts_result.scalars().all()}
    else:
        accounts = {}

    # Compute total wealth and sort
    agents_with_wealth = []
    for agent in all_agents:
        bank_bal = accounts.get(agent.id, 0.0)
        total_wealth = float(agent.balance) + bank_bal
        agents_with_wealth.append((agent, bank_bal, total_wealth))

    agents_with_wealth.sort(key=lambda x: x[2], reverse=True)

    # Apply pagination
    page_slice = agents_with_wealth[offset : offset + page_size]

    # Get housing zones and business counts for the page
    page_agent_ids = [a.id for a, _, _ in page_slice]

    # Housing zones
    housing_zone_ids = {a.housing_zone_id for a, _, _ in page_slice if a.housing_zone_id}
    zones_map: dict = {}
    if housing_zone_ids:
        zones_result = await db.execute(select(Zone).where(Zone.id.in_(list(housing_zone_ids))))
        zones_map = {z.id: z for z in zones_result.scalars().all()}

    # Business counts per agent
    biz_counts: dict = {}
    if page_agent_ids:
        biz_count_result = await db.execute(
            select(
                Business.owner_id,
                func.count(Business.id).label("cnt"),
            )
            .where(
                and_(
                    Business.owner_id.in_(page_agent_ids),
                    Business.closed_at.is_(None),
                    Business.is_npc.is_(False),
                )
            )
            .group_by(Business.owner_id)
        )
        biz_counts = {row.owner_id: int(row.cnt) for row in biz_count_result.all()}

    # Employment status per agent
    employed_ids: set = set()
    if page_agent_ids:
        emp_result = await db.execute(
            select(func.distinct(Employment.agent_id)).where(
                and_(
                    Employment.agent_id.in_(page_agent_ids),
                    Employment.terminated_at.is_(None),
                )
            )
        )
        employed_ids = {row[0] for row in emp_result.all()}

    now = datetime.now(UTC)

    # Classify strategies for agents on this page
    from backend.spectator.strategy import classify_agent

    redis = request.app.state.redis
    settings = request.app.state.settings
    strategy_map: dict = {}
    for agent, _bb, _tw in page_slice:
        try:
            classification = await classify_agent(db, str(agent.id), redis, settings=settings)
            strategy_map[agent.id] = classification.get("strategy", "unknown")
        except Exception:
            strategy_map[agent.id] = "unknown"

    agents_list = []
    for agent, bank_bal, total_wealth in page_slice:
        housing_zone = None
        if agent.housing_zone_id and agent.housing_zone_id in zones_map:
            z = zones_map[agent.housing_zone_id]
            housing_zone = {"slug": z.slug, "name": z.name}

        agents_list.append(
            {
                "id": str(agent.id),
                "name": agent.name,
                "model": agent.model,
                "balance": round(float(agent.balance), 2),
                "bank_balance": round(bank_bal, 2),
                "total_wealth": round(total_wealth, 2),
                "housing_zone": housing_zone,
                "businesses_count": biz_counts.get(agent.id, 0),
                "is_employed": agent.id in employed_ids,
                "bankruptcy_count": agent.bankruptcy_count,
                "is_active": agent.is_active,
                "is_jailed": agent.is_jailed(now),
                "created_at": agent.created_at.isoformat(),
                "strategy": strategy_map.get(agent.id, "unknown"),
            }
        )

    return {
        "agents": agents_list,
        "total": total,
    }


@router.get("/agents/{agent_id}")
async def get_agent_profile(
    request: Request,
    agent_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Public agent profile with detailed info.
    """
    try:
        uid = _uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent_id format")

    result = await db.execute(select(Agent).where(Agent.id == uid))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    now = datetime.now(UTC)

    # Housing zone
    housing_zone = None
    if agent.housing_zone_id:
        zone_result = await db.execute(select(Zone).where(Zone.id == agent.housing_zone_id))
        zone = zone_result.scalar_one_or_none()
        if zone:
            housing_zone = {"slug": zone.slug, "name": zone.name}

    # Bank account
    bank_result = await db.execute(select(BankAccount).where(BankAccount.agent_id == agent.id))
    bank_account = bank_result.scalar_one_or_none()
    bank_balance = float(bank_account.balance) if bank_account else 0.0

    # Employment
    employment_result = await db.execute(
        select(Employment, Business)
        .join(Business, Business.id == Employment.business_id)
        .where(
            and_(
                Employment.agent_id == agent.id,
                Employment.terminated_at.is_(None),
            )
        )
    )
    emp_row = employment_result.first()
    employment = None
    if emp_row:
        emp, biz = emp_row
        employment = {
            "business_id": str(biz.id),
            "business_name": biz.name,
            "product_slug": emp.product_slug,
            "wage_per_work": float(emp.wage_per_work),
        }

    # Owned businesses
    owned_biz_result = await db.execute(
        select(Business).where(
            and_(
                Business.owner_id == agent.id,
                Business.closed_at.is_(None),
                Business.is_npc.is_(False),
            )
        )
    )
    businesses = []
    for biz in owned_biz_result.scalars().all():
        # Get zone slug
        biz_zone_result = await db.execute(select(Zone).where(Zone.id == biz.zone_id))
        biz_zone = biz_zone_result.scalar_one_or_none()
        businesses.append(
            {
                "id": str(biz.id),
                "name": biz.name,
                "type_slug": biz.type_slug,
                "zone_slug": biz_zone.slug if biz_zone else str(biz.zone_id),
            }
        )

    # Inventory
    inv_result = await db.execute(
        select(InventoryItem).where(
            and_(
                InventoryItem.owner_type == "agent",
                InventoryItem.owner_id == agent.id,
                InventoryItem.quantity > 0,
            )
        )
    )
    inventory = [{"good_slug": item.good_slug, "quantity": item.quantity} for item in inv_result.scalars().all()]

    # Criminal record
    violations_result = await db.execute(select(func.count(Violation.id)).where(Violation.agent_id == agent.id))
    violation_count = violations_result.scalar() or 0

    jailed = agent.is_jailed(now)

    # Recent transactions (last 10)
    txn_result = await db.execute(
        select(Transaction)
        .where(
            or_(
                Transaction.from_agent_id == agent.id,
                Transaction.to_agent_id == agent.id,
            )
        )
        .order_by(desc(Transaction.created_at))
        .limit(10)
    )
    transactions_recent = [
        {
            "type": t.type,
            "amount": float(t.amount),
            "created_at": t.created_at.isoformat(),
        }
        for t in txn_result.scalars().all()
    ]

    # Strategy classification and badges
    from backend.spectator.badges import compute_badges
    from backend.spectator.strategy import classify_agent

    redis = request.app.state.redis
    clock = request.app.state.clock
    settings = request.app.state.settings

    try:
        strategy_data = await classify_agent(db, agent_id, redis, settings=settings)
    except Exception:
        strategy_data = {"strategy": "unknown", "traits": []}

    try:
        badges = await compute_badges(db, agent_id, redis, clock)
    except Exception:
        badges = []

    return {
        "id": str(agent.id),
        "name": agent.name,
        "model": agent.model,
        "balance": round(float(agent.balance), 2),
        "bank_balance": round(bank_balance, 2),
        "total_wealth": round(float(agent.balance) + bank_balance, 2),
        "housing_zone": housing_zone,
        "employment": employment,
        "businesses": businesses,
        "inventory": inventory,
        "criminal_record": {
            "violation_count": violation_count,
            "jailed": jailed,
            "jail_until": agent.jail_until.isoformat() if agent.jail_until else None,
        },
        "bankruptcy_count": agent.bankruptcy_count,
        "is_active": agent.is_active,
        "created_at": agent.created_at.isoformat(),
        "transactions_recent": transactions_recent,
        "strategy": strategy_data,
        "badges": badges,
    }
