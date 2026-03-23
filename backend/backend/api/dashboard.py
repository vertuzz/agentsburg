"""
API endpoints: private dashboard (view_token required).

Includes agent status, transactions, businesses, and messages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.agent import Agent
from backend.models.banking import BankAccount
from backend.models.business import Business, Employment, StorefrontPrice
from backend.models.government import Violation
from backend.models.inventory import InventoryItem
from backend.models.message import Message
from backend.models.transaction import Transaction
from backend.models.zone import Zone

from backend.api.common import get_agent_from_view_token

router = APIRouter(tags=["api"])


@router.get("/agent")
async def get_agent_status(
    token: str = Query(..., description="Agent view token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Full agent status for the private dashboard.

    Requires view_token query parameter.
    """
    agent = await get_agent_from_view_token(token, db)

    # Housing zone
    housing_zone = None
    if agent.housing_zone_id:
        zone_result = await db.execute(
            select(Zone).where(Zone.id == agent.housing_zone_id)
        )
        zone = zone_result.scalar_one_or_none()
        if zone:
            housing_zone = {"id": str(zone.id), "slug": zone.slug, "name": zone.name}

    # Bank account
    bank_result = await db.execute(
        select(BankAccount).where(BankAccount.agent_id == agent.id)
    )
    bank_account = bank_result.scalar_one_or_none()
    bank_balance = float(bank_account.balance) if bank_account else 0.0

    # Employment
    employment_result = await db.execute(
        select(Employment, Business).join(
            Business, Business.id == Employment.business_id
        ).where(
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
            "hired_at": emp.hired_at.isoformat(),
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
    owned_businesses = []
    for biz in owned_biz_result.scalars().all():
        owned_businesses.append({
            "id": str(biz.id),
            "name": biz.name,
            "type_slug": biz.type_slug,
            "zone_id": str(biz.zone_id),
        })

    # Criminal record
    violations_result = await db.execute(
        select(Violation)
        .where(Violation.agent_id == agent.id)
        .order_by(desc(Violation.detected_at))
        .limit(10)
    )
    violations = [
        {
            "type": v.type,
            "fine_amount": float(v.fine_amount),
            "detected_at": v.detected_at.isoformat(),
            "jail_until": v.jail_until.isoformat() if v.jail_until else None,
        }
        for v in violations_result.scalars().all()
    ]

    now = datetime.now(timezone.utc)
    jailed = agent.jail_until is not None and agent.jail_until > now
    jail_remaining_seconds = None
    if jailed and agent.jail_until:
        jail_remaining_seconds = (agent.jail_until - now).total_seconds()

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
    inventory = [
        {"good_slug": item.good_slug, "quantity": item.quantity}
        for item in inv_result.scalars().all()
    ]

    return {
        "id": str(agent.id),
        "name": agent.name,
        "model": agent.model,
        "balance": float(agent.balance),
        "bank_balance": bank_balance,
        "total_wealth": float(agent.balance) + bank_balance,
        "housing_zone": housing_zone,
        "employment": employment,
        "businesses": owned_businesses,
        "criminal_record": {
            "violation_count": agent.violation_count,
            "jailed": jailed,
            "jail_until": agent.jail_until.isoformat() if agent.jail_until else None,
            "jail_remaining_seconds": jail_remaining_seconds,
            "recent_violations": violations,
        },
        "inventory": inventory,
        "bankruptcy_count": agent.bankruptcy_count,
        "is_active": agent.is_active,
        "created_at": agent.created_at.isoformat(),
    }


@router.get("/agent/transactions")
async def get_agent_transactions(
    token: str = Query(..., description="Agent view token"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Paginated transaction history for an agent (newest first).
    """
    agent = await get_agent_from_view_token(token, db)
    offset = (page - 1) * page_size

    # Count total
    count_result = await db.execute(
        select(func.count(Transaction.id)).where(
            or_(
                Transaction.from_agent_id == agent.id,
                Transaction.to_agent_id == agent.id,
            )
        )
    )
    total = count_result.scalar() or 0

    # Fetch page
    txn_result = await db.execute(
        select(Transaction).where(
            or_(
                Transaction.from_agent_id == agent.id,
                Transaction.to_agent_id == agent.id,
            )
        )
        .order_by(desc(Transaction.created_at))
        .offset(offset)
        .limit(page_size)
    )
    txns = txn_result.scalars().all()

    transactions = [
        {
            "id": str(t.id),
            "type": t.type,
            "amount": float(t.amount),
            "from_agent_id": str(t.from_agent_id) if t.from_agent_id else None,
            "to_agent_id": str(t.to_agent_id) if t.to_agent_id else None,
            "direction": "in" if t.to_agent_id == agent.id else "out",
            "metadata": t.metadata_json,
            "created_at": t.created_at.isoformat(),
        }
        for t in txns
    ]

    return {
        "transactions": transactions,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        },
    }


@router.get("/agent/businesses")
async def get_agent_businesses(
    token: str = Query(..., description="Agent view token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Detailed business info for all businesses owned by the agent.
    """
    agent = await get_agent_from_view_token(token, db)

    biz_result = await db.execute(
        select(Business).where(
            and_(
                Business.owner_id == agent.id,
                Business.is_npc.is_(False),
            )
        )
    )
    businesses = biz_result.scalars().all()

    result = []
    for biz in businesses:
        # Zone info
        zone_result = await db.execute(
            select(Zone).where(Zone.id == biz.zone_id)
        )
        zone = zone_result.scalar_one_or_none()

        # Inventory
        inv_result = await db.execute(
            select(InventoryItem).where(
                and_(
                    InventoryItem.owner_type == "business",
                    InventoryItem.owner_id == biz.id,
                    InventoryItem.quantity > 0,
                )
            )
        )
        inventory = [
            {"good_slug": item.good_slug, "quantity": item.quantity}
            for item in inv_result.scalars().all()
        ]

        # Storefront prices
        prices_result = await db.execute(
            select(StorefrontPrice).where(StorefrontPrice.business_id == biz.id)
        )
        storefront_prices = [
            {"good_slug": sp.good_slug, "price": float(sp.price)}
            for sp in prices_result.scalars().all()
        ]

        # Active employees
        emp_result = await db.execute(
            select(Employment).where(
                and_(
                    Employment.business_id == biz.id,
                    Employment.terminated_at.is_(None),
                )
            )
        )
        employees = emp_result.scalars().all()
        employee_list = [
            {
                "agent_id": str(e.agent_id),
                "product_slug": e.product_slug,
                "wage_per_work": float(e.wage_per_work),
                "hired_at": e.hired_at.isoformat(),
            }
            for e in employees
        ]

        # Revenue last 7d
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        rev_result = await db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                and_(
                    Transaction.type.in_(["storefront", "marketplace"]),
                    Transaction.to_agent_id == agent.id,
                    Transaction.created_at >= seven_days_ago,
                )
            )
        )
        revenue_7d = float(rev_result.scalar() or 0)

        result.append({
            "id": str(biz.id),
            "name": biz.name,
            "type_slug": biz.type_slug,
            "zone": {"id": str(zone.id), "slug": zone.slug, "name": zone.name} if zone else None,
            "storage_capacity": biz.storage_capacity,
            "is_open": biz.is_open(),
            "closed_at": biz.closed_at.isoformat() if biz.closed_at else None,
            "inventory": inventory,
            "storefront_prices": storefront_prices,
            "employees": employee_list,
            "revenue_7d": revenue_7d,
            "created_at": biz.created_at.isoformat(),
        })

    return {"businesses": result}


@router.get("/agent/messages")
async def get_agent_messages(
    token: str = Query(..., description="Agent view token"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Paginated messages (inbox) for the agent, newest first.
    """
    agent = await get_agent_from_view_token(token, db)
    offset = (page - 1) * page_size

    # Count total inbox messages
    count_result = await db.execute(
        select(func.count(Message.id)).where(Message.to_agent_id == agent.id)
    )
    total = count_result.scalar() or 0

    # Fetch page
    msg_result = await db.execute(
        select(Message)
        .where(Message.to_agent_id == agent.id)
        .order_by(desc(Message.created_at))
        .offset(offset)
        .limit(page_size)
    )
    messages = msg_result.scalars().all()

    # Resolve sender names
    sender_ids = list({m.from_agent_id for m in messages})
    senders: dict = {}
    if sender_ids:
        senders_result = await db.execute(
            select(Agent.id, Agent.name).where(Agent.id.in_(sender_ids))
        )
        senders = {row.id: row.name for row in senders_result.all()}

    messages_list = [
        {
            "id": str(m.id),
            "from_agent_id": str(m.from_agent_id),
            "from_agent_name": senders.get(m.from_agent_id, "Unknown"),
            "text": m.text,
            "read": m.read,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]

    # Unread count
    unread_result = await db.execute(
        select(func.count(Message.id)).where(
            and_(Message.to_agent_id == agent.id, Message.read.is_(False))
        )
    )
    unread_count = unread_result.scalar() or 0

    return {
        "messages": messages_list,
        "unread_count": unread_count,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        },
    }


@router.get("/transactions/recent")
async def get_transactions_recent(
    type: str | None = Query(None, description="Comma-separated transaction types filter"),
    limit: int = Query(50, ge=1, le=100, description="Number of transactions to return"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Recent public transaction feed (newest first).
    """
    filters = []
    if type:
        type_slugs = [t.strip() for t in type.split(",") if t.strip()]
        if type_slugs:
            filters.append(Transaction.type.in_(type_slugs))

    query = select(Transaction)
    if filters:
        query = query.where(and_(*filters))
    query = query.order_by(desc(Transaction.created_at)).limit(limit)

    txn_result = await db.execute(query)
    txns = txn_result.scalars().all()

    # Resolve agent names
    agent_ids = set()
    for t in txns:
        if t.from_agent_id:
            agent_ids.add(t.from_agent_id)
        if t.to_agent_id:
            agent_ids.add(t.to_agent_id)

    agents_map: dict = {}
    if agent_ids:
        agents_result = await db.execute(
            select(Agent).where(Agent.id.in_(list(agent_ids)))
        )
        agents_map = {a.id: a.name for a in agents_result.scalars().all()}

    transactions = [
        {
            "id": str(t.id),
            "type": t.type,
            "amount": float(t.amount),
            "from_agent_name": agents_map.get(t.from_agent_id) if t.from_agent_id else None,
            "to_agent_name": agents_map.get(t.to_agent_id) if t.to_agent_id else None,
            "metadata": t.metadata_json,
            "created_at": t.created_at.isoformat(),
        }
        for t in txns
    ]

    return {"transactions": transactions}
